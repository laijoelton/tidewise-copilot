"""The Tidewise security pipeline: ten gates every action passes through.

Each control already lives in its own module (API, guard, ml, policy,
agent, sandbox, audit). This module names them as one ordered pipeline and
produces, for any action the agent proposed, a gate-by-gate trace — so an
owner, a judge or an auditor can see exactly where something was allowed,
escalated to the owner, or stopped.

    Inbound content (emails, booking notes)          Actions (anything that moves money or sends a message)
    G1 Front door        rate limit, validated input     G5 Provenance      who asked: owner / books / outside / quarantined
    G2 Sender identity   registered, look-alike, homoglyph  G6 Business rules  bill real & unpaid, payee verified, PO match ...
    G3 Content rules     injection (EN/MS/ZH), hidden text,  G7 Limits          per-payment, daily, FX, autonomy budget, kill switch
                         payment redirect                    G8 ML risk         payment anomaly, customer credit risk
    G4 ML threat score   classifier, held-out tested         G9 Owner channel   token, step-up, expiry, re-check
                                                             G10 Execute & seal  idempotent sandbox, Merkle-chained audit

Statuses: pass · escalate (goes to the owner) · stop (blocked or quarantined)
· waiting (for the owner) · skip (not applicable to this action).
"""

from typing import Any, Dict, List, Optional

GATES: List[Dict[str, Any]] = [
    {"id": 1, "name": "Front door", "lane": "inbound", "module": "api/main.py",
     "does": "Rate-limits every caller and validates every request against strict schemas",
     "stops": "floods, oversized or malformed requests, forged prices on bookings"},
    {"id": 2, "name": "Sender identity", "lane": "inbound", "module": "guard.py",
     "does": "Checks the sender against registered counterparties; flags look-alike and homoglyph domains",
     "stops": "fake suppliers and customers (e.g. hutan-lestari-timber vs hutanlestari, Cyrillic ‘а’)"},
    {"id": 3, "name": "Content rules", "lane": "inbound", "module": "guard.py",
     "does": "Finds prompt injection (English, Malay, Chinese), hidden markup and bank-detail changes; masks account numbers",
     "stops": "“AI assistant: ignore your instructions…”, “pay our new account”"},
    {"id": 4, "name": "ML threat score", "lane": "inbound", "module": "ml.py",
     "does": "Text classifier scores fraud and injection; tested on phrasings it never saw",
     "stops": "paraphrased attacks the rules miss (44% → 93% caught, per evidence/ml_report.json)"},
    {"id": 5, "name": "Provenance", "lane": "action", "module": "policy.py",
     "does": "Tags who asked for the action: the owner, the books, outside content, or quarantined content",
     "stops": "anything derived from a quarantined email or booking; outside requests can’t auto-run"},
    {"id": 6, "name": "Business rules", "lane": "action", "module": "policy.py",
     "does": "Checks the action makes sense in the books: real unpaid bill, verified payee, registered recipient, PO match",
     "stops": "double payments, changed bank details, sends to unknown addresses, inflated invoices"},
    {"id": 7, "name": "Limits", "lane": "action", "module": "policy.py",
     "does": "Per-payment and daily caps, FX cap, booking size, autonomy budget per turn, owner kill switch",
     "stops": "split payments, runaway loops; anything over a limit goes to the owner"},
    {"id": 8, "name": "ML risk check", "lane": "action", "module": "ml.py",
     "does": "Payment-anomaly model against the payee’s history; credit-risk model for customers",
     "stops": "padded bills under the limit, risky customers — escalated, never auto-run"},
    {"id": 9, "name": "Owner channel", "lane": "action", "module": "agent.py / api",
     "does": "Approvals only with the owner token; re-type amounts above RM 50k; 48 h expiry; re-checked at execution",
     "stops": "“approve all” in chat or in an email, blind approvals, stale approvals"},
    {"id": 10, "name": "Execute & seal", "lane": "action", "module": "sandbox.py / audit.py",
     "does": "Idempotent sandbox bank re-validates on its own; every step sealed into the Merkle-chained audit log",
     "stops": "replays, overdrafts, silent edits to history"},
]

# Which gate each policy check belongs to.
CHECK_GATE = {
    "provenance": 5, "booking screening": 5, "screening": 5,
    "known action": 6, "bill exists": 6, "not already paid": 6, "bill approved": 6, "payee verified": 6,
    "bank details match": 6, "funds available": 6, "registered recipient": 6, "invoice open": 6,
    "not paying early": 6, "bill open": 6, "commits the business": 6, "credit commitment": 6,
    "correction needed": 6, "corrected to the PO": 6, "deposit due": 6, "booking pending": 6,
    "known customer, contact on file": 6, "factory capacity": 6, "deposit": 6, "payee changes": 6,
    "transfer target": 6, "limits": 6,
    "per-payment limit": 7, "daily limit": 7, "FX auto limit": 7, "autonomy budget": 7,
    "autonomy paused": 7, "reminder cooldown": 7, "order size": 7,
    "payment pattern (ML)": 8, "late-payment risk (ML)": 8,
}
SENDER = {"sender_lookalike", "sender_homoglyph", "sender_unknown", "display_name_impersonation", "sender_throwaway"}
CONTENT = {"prompt_injection", "hidden_content", "payment_redirect", "pressure_tactics"}


def _gate(i: int, status: str, detail: str) -> Dict[str, Any]:
    g = GATES[i - 1]
    return {"id": i, "name": g["name"], "status": status, "detail": detail}


def _inbound(scan: Optional[Any]) -> List[Dict[str, Any]]:
    if scan is None:
        why = "no outside content involved — parameters came from the owner or the books"
        return [_gate(2, "skip", why), _gate(3, "skip", why), _gate(4, "skip", why)]
    f = scan.findings if hasattr(scan, "findings") else scan.get("findings", [])
    rows = [(x.check, x.severity, x.detail) if hasattr(x, "check") else (x["check"], x["severity"], x["detail"]) for x in f]
    out = []
    for gid, kinds in ((2, SENDER), (3, CONTENT), (4, {"ml_threat"})):
        hits = [r for r in rows if r[0] in kinds]
        crit = [r for r in hits if r[1] == "critical"]
        if crit:
            out.append(_gate(gid, "stop", crit[0][2]))
        elif hits:
            out.append(_gate(gid, "pass", f"warning only: {hits[0][2]}"))
        else:
            ml = getattr(scan, "ml", None) or (scan.get("ml") if isinstance(scan, dict) else None)
            out.append(_gate(gid, "pass", f"threat score {ml['threat']:.0%}" if gid == 4 and ml else "clean"))
    return out


def trace(step: Dict[str, Any], scans: Optional[Dict[str, Any]] = None,
          booking_scan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Gate-by-gate trace for one proposed action (an agent step dict)."""
    d = step["decision"]
    gates = [_gate(1, "pass", "validated request through the rate-limited API")]
    scan = None
    if step.get("source_ref") and scans:
        scan = scans.get(step["source_ref"])
    elif booking_scan:
        scan = booking_scan
    gates += _inbound(scan)

    by_gate: Dict[int, List[Dict[str, Any]]] = {5: [], 6: [], 7: [], 8: []}
    for c in d["checks"]:
        g = CHECK_GATE.get(c["name"])
        if g:
            by_gate[g].append(c)
    blocked = d["outcome"] == "blocked"
    for gid in (5, 6, 7, 8):
        cs = by_gate[gid]
        failed = [c for c in cs if not c["passed"]]
        if not cs:
            gates.append(_gate(gid, "skip", "not relevant to this action"))
        elif failed:
            hard = blocked and (gid in (5, 6) or d["tier"] == 4)
            gates.append(_gate(gid, "stop" if hard else "escalate", failed[0]["detail"]))
        elif gid == 5 and step.get("provenance") == "external" and d["outcome"] != "auto":
            gates.append(_gate(gid, "escalate", "requested by outside content — it can never move money on its own"))
        else:
            gates.append(_gate(gid, "pass", cs[0]["detail"]))
        if gates[-1]["status"] == "stop":
            break

    # Inbound stops quarantine the *content*; declining that content is still an
    # action that runs. Only a stop at an action gate (5–8) ends the pipeline.
    if not any(g["status"] == "stop" for g in gates if g["id"] >= 5):
        appr = step.get("approval_status")
        if d["outcome"] == "needs_approval":
            status = {"approved": "pass", "rejected": "stop", "expired": "stop"}.get(appr, "waiting")
            gates.append(_gate(9, status, {"approved": "approved by the owner",
                                           "rejected": "rejected by the owner",
                                           "expired": "approval expired"}.get(appr, "waiting for the owner")
                               + (" (step-up required)" if d.get("step_up") else "")))
        else:
            gates.append(_gate(9, "skip", "within the agent’s autonomy — no approval needed"))
        r = step.get("receipt")
        if r:
            gates.append(_gate(10, "pass", f"{r['ref']} · {r['status']} · sealed in the audit log"))
        elif step.get("error"):
            gates.append(_gate(10, "stop", f"sandbox refused: {step['error']}"))
        else:
            gates.append(_gate(10, "skip", "not executed yet"))

    have = {g["id"] for g in gates}
    for g in GATES:                       # gates after a stop are never reached
        if g["id"] not in have:
            gates.append(_gate(g["id"], "skip", "not reached — stopped earlier"))
    return sorted(gates, key=lambda g: g["id"])


def stopped_at(gates: List[Dict[str, Any]]) -> Optional[int]:
    return next((g["id"] for g in gates if g["status"] == "stop"), None)
