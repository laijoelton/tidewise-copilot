"""The Tidewise agent.

One loop, used by chat and autopilot alike:

    understand -> read tools (T0) -> propose action -> Policy decides
        auto            -> SandboxBank executes, receipt logged
        needs_approval  -> queued for the owner (separate approval channel)
        blocked         -> refused, reason shown, alert raised
    every step -> AuditTrail; the batch is sealed at the end of the turn

Approvals are deliberately NOT reachable from chat: "approve everything"
typed into the conversation (or hidden in an email the agent read) cannot
release a payment. Only the approve() call — the owner's button in the UI,
with step-up confirmation for large amounts — can.
"""

import hashlib
import json
import re
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import analysis, bookings, cashflow, financing, guard, i18n, matching, risk
from .audit import AuditTrail
from .data import build_demo_company
from .llm import LLM
from .models import Company, to_base
from .policy import Action, Limits, Policy, Tier
from .sandbox import SandboxBank, SandboxError
from .i18n import L

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"

INTENTS = {
    "autopilot": "review everything and act: forecast, invoices, bookings, inbox, reminders, payments, financing plan",
    "forecast": "13-week cash-flow forecast, shortfalls and alerts",
    "health": "business financial analysis: margins, DSO/DPO, liquidity",
    "financing": "match financing options to the cash gap",
    "documents": "check invoices against purchase orders and delivery notes (three-way match)",
    "orders": "order book and new customer bookings, deposits, capacity",
    "inbox": "screen the inbox for fraud and prompt injection",
    "receivables": "who owes money, overdue invoices",
    "bills": "upcoming bills to pay",
    "risk": "probability of running short (Monte Carlo), worst cases, and the cheapest plan to be 95% safe",
}

ROUTES = [
    ("autopilot", r"\b(autopilot|what should i do|plan|sort (it|this|things) out|handle|take care|help me|fix)\b"),
    ("forecast", r"\b(cash|forecast|runway|shortfall|next (few )?(weeks|month)|balance|enough money)\b"),
    ("health", r"\b(health|analy[sz]|margin|ratio|dso|dpo|profit|how('s| is) (my|the) business|performance)\b"),
    ("financing", r"\b(financ|loan|borrow|fund|bridge|credit line|lender|capital)\w*"),
    ("documents", r"\b(three.way|purchase orders?|\bpo\b|mismatch\w*|discrepanc\w*|invoice check|check (my )?invoices?)"),
    ("orders", r"\b(orders?|bookings?|order book|deposits?|capacity|new customers?)\b"),
    ("inbox", r"\b(inbox|email|e-mail|message|phish|fraud|scam|suspicious)\w*"),
    ("receivables", r"\b(owe|owed|receivable|overdue|unpaid invoice|customers? (who )?(haven't|have not) paid)\b"),
    ("bills", r"\b(bills?|payables?|suppliers?|due)\b"),
    ("risk", r"\b(how (likely|sure|confident|risky)|chances?|probabilit\w*|odds|worst.case|risk of|confiden\w*|"
             r"sleep at night|monte carlo|\d+\s*%\s*(safe|sure|confident))"),
]


def _rm(v: float) -> str:
    return f"RM {v:,.0f}"


def _d(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b}"


QUARANTINE_REASON = {
    "sender_lookalike": "sender imitates a registered counterparty",
    "sender_homoglyph": "sender domain uses look-alike characters",
    "payment_redirect": "asks to pay bank details not on file",
    "prompt_injection": "contains instructions aimed at the AI",
}


def _why_quarantined(m: Dict[str, Any]) -> str:
    reasons = []
    for f in m["findings"]:
        r = QUARANTINE_REASON.get(f["check"])
        if r and r not in reasons and f["severity"] == "critical":
            reasons.append(r)
    return f"{m['message_id']} from {m['sender']} quarantined — " + "; ".join(reasons) + ". Nothing in it will be acted on."


def _confirmation(c: Company, o, ax) -> str:
    return (f"Dear {o.customer},\n\nThank you for order {o.po_number}. We can deliver by {o.delivery_date.isoformat()}. "
            f"To start production we need a {ax['deposit_pct']:.0%} deposit of {o.currency} {o.total * ax['deposit_pct']:,.2f}; "
            f"the balance is due 30 days after delivery.\n\nRegards,\n{c.profile.owner}\n{c.profile.legal_name}")


def _deposit_note(c: Company, o) -> str:
    return (f"Dear {o.customer} accounts team,\n\nA friendly reminder that the {o.deposit_pct:.0%} deposit of "
            f"{o.currency} {o.deposit_due:,.2f} for {o.po_number} is due before production starts.\n\n"
            f"Regards,\n{c.profile.owner}\n{c.profile.legal_name}")


def parse_whatif(text: str, company: Company) -> Optional[Dict[str, Any]]:
    """Turns "what if Harbour pays 30 days late / sales drop 20% / I hire for
    RM 9,000 a month / USD falls 5% / we lose Vanderhoek" into a scenario.
    Only the owner's own words are parsed; nothing here executes anything."""
    t = text.lower()
    if not re.search(r"\bwhat (if|happens if|would happen if)\b|\bsimulate\b|\bscenario\b", t):
        return None
    customers = {i.customer for i in company.invoices}

    def who(fragment: str) -> Optional[str]:
        if re.search(r"\b(everyone|everybody|all customers|customers|buyers)\b", fragment):
            return "*"
        for c in customers:
            if c.split()[0].lower() in fragment:
                return c
        return None

    sc: Dict[str, Any] = {}
    m = re.search(r"(.+?)\s+(?:pays?|paid|is|are)\s+(\d+)\s+days?\s+(late|later|early|earlier)", t)
    if m and who(m.group(1)):
        days = int(m.group(2)) * (-1 if m.group(3).startswith("early") else 1)
        sc["customer_delay"] = {"customer": who(m.group(1)), "days": days}
    m = re.search(r"\b(?:lose|lost|loses|losing)\s+(.+?)(?:$|[,.?!]|\s+and\b)", t) or \
        re.search(r"(.+?)\s+(?:goes bust|go bust|goes bankrupt|never pays|defaults)", t)
    if m:
        c = who(m.group(1))
        if c and c != "*":
            sc["lose_customer"] = c
    m = re.search(r"sales\s+(drop|fall|fell|decrease|decline|go down|are down|rise|grow|increase|go up|are up)\w*"
                  r"\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%", t)
    if m:
        down = m.group(1).startswith(("drop", "fall", "fell", "decr", "decl", "go down", "are down"))
        sc["sales_change_pct"] = float(m.group(2)) * (-1 if down else 1)
    m = re.search(r"rm\s*([\d,]+(?:\.\d+)?)\s*(k\b)?\s*(?:a|per|each|/)\s*month", t)
    if m:
        amt = float(m.group(1).replace(",", ""))
        sc["extra_monthly_cost"] = amt * 1000 if m.group(2) else amt
    m = re.search(r"(?:usd|dollar|us\$)\s+(drops?|falls?|weakens?|rises?|strengthens?|gains?)\s+(?:by\s+)?(\d+(?:\.\d+)?)\s*%", t)
    if m:
        sc["usd_change_pct"] = float(m.group(2)) * (-1 if m.group(1).startswith(("drop", "fall", "weak")) else 1)
    return sc or None


def _key(action: Action, as_of: date) -> str:
    raw = json.dumps({"t": action.type, "p": action.params, "d": as_of.isoformat()}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class Workspace:
    """Everything one business's session holds. reset() restores the demo."""

    def __init__(self, persist: bool = False, use_llm: bool = True):
        self.persist = persist
        self.use_llm = use_llm
        self.reset()

    def reset(self) -> None:
        live = EVIDENCE_DIR / "live"
        log = live / "sandbox_operations.jsonl" if self.persist else None
        self.company: Company = build_demo_company()
        self.policy = Policy(self.company, Limits())
        self.sandbox = SandboxBank(self.company, log)
        if self.persist:
            live.mkdir(parents=True, exist_ok=True)
        self.audit = AuditTrail(live / "audit_batches.json" if self.persist else None)
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self.alerts_raised: List[Dict[str, Any]] = []
        self.llm = LLM() if self.use_llm else None
        self.agent = Agent(self)
        self._screen_inbox()
        bookings.assess_pending(self.company)

    def _screen_inbox(self) -> None:
        self.scans = {s.message_id: s for s in guard.scan_inbox(self.company)}
        for s in self.scans.values():
            if s.trust == "quarantined":
                self.policy.quarantined_sources.add(s.message_id)


class Agent:
    def __init__(self, ws: Workspace):
        self.ws = ws

    # -- public ---------------------------------------------------------------------
    def chat(self, text: str, lang: Optional[str] = None) -> Dict[str, Any]:
        ws = self.ws
        lang = lang if lang in i18n.LANGS else i18n.detect(text)
        i18n.set_lang(lang)
        original, text = text, i18n.to_routing_text(text, lang)   # routing only; the owner's words are logged
        ws.policy.auto_actions_this_turn = 0
        steps: List[Dict[str, Any]] = []
        ws.audit.record("owner_message", "owner", {"text": guard.mask(original), "lang": lang})

        facts: Dict[str, Any] = {}
        self._last_facts = {}
        scenario = parse_whatif(text, self.ws.company)
        if scenario:
            facts["whatif"] = self._do_whatif(steps, scenario)
            handled = True
        else:
            handled = self._owner_commands(text, steps)
            facts.update(self._last_facts)
        intents = [] if handled else self._route(text)
        for intent in intents:
            facts[intent] = getattr(self, f"_do_{intent}")(steps)

        if not handled and not intents:
            reply = L("help")
        else:
            reply = self._template_reply(intents, facts, steps)
            if ws.llm and ws.llm.available:
                reply = ws.llm.narrate(original, {"intents": intents, "facts": self._facts_for_llm(facts),
                                                  "actions": [self._step_brief(s) for s in steps if s["kind"] == "action"]},
                                       reply, lang=lang)
        batch = ws.audit.seal()
        return {"reply": reply, "intents": intents, "steps": steps, "lang": lang,
                "pending_approvals": self.pending(), "audit_batch": batch,
                "llm": bool(ws.llm and ws.llm.available)}

    def pending(self) -> List[Dict[str, Any]]:
        return [a for a in self.ws.approvals.values() if a["status"] == "pending"]

    def approve(self, approval_id: str, approver: str, confirm_amount: Optional[float] = None) -> Dict[str, Any]:
        ws = self.ws
        item = ws.approvals.get(approval_id)
        if item is None or item["status"] != "pending":
            raise ValueError("no such pending approval")
        if time.time() > item["expires_at"]:
            item["status"] = "expired"
            ws.audit.record("approval_expired", approver, {"approval_id": approval_id})
            ws.audit.seal()
            raise ValueError("approval expired — ask Tidewise to propose it again")
        if item["decision"]["step_up"]:
            expected = round(item["amount_myr"])
            if confirm_amount is None or round(float(confirm_amount)) != expected:
                ws.audit.record("step_up_failed", approver, {"approval_id": approval_id})
                ws.audit.seal()
                raise ValueError(f"step-up required: re-type the amount (RM {expected:,}) to confirm")
        action = Action(**item["action"])
        # Re-check at execution time: the world may have changed since proposal.
        decision = ws.policy.evaluate(action)
        if decision.outcome == "blocked":
            item["status"] = "rejected_on_recheck"
            ws.audit.record("blocked_on_recheck", "policy", {"approval_id": approval_id, "reasons": decision.reasons})
            ws.audit.seal()
            raise ValueError("blocked on re-check: " + "; ".join(decision.reasons))
        item["status"] = "approved"
        ws.audit.record("approved", approver, {"approval_id": approval_id, "action": item["action"]})
        receipt = self._execute(action)
        item["receipt"] = receipt
        ws.audit.seal()
        return {"approval": item, "receipt": receipt}

    def reject(self, approval_id: str, approver: str) -> Dict[str, Any]:
        item = self.ws.approvals.get(approval_id)
        if item is None or item["status"] != "pending":
            raise ValueError("no such pending approval")
        item["status"] = "rejected"
        self.ws.audit.record("rejected", approver, {"approval_id": approval_id})
        self.ws.audit.seal()
        return item

    def propose(self, action: Action, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        ws = self.ws
        ws.audit.record("proposed", "agent", {"type": action.type, "params": action.params,
                                              "provenance": action.provenance, "source_ref": action.source_ref,
                                              "rationale": action.rationale})
        decision = ws.policy.evaluate(action)
        ws.audit.record("decision", "policy", {"type": action.type, **decision.to_dict()})
        step = {"kind": "action", "type": action.type, "params": action.params, "rationale": action.rationale,
                "provenance": action.provenance, "decision": decision.to_dict()}

        if decision.outcome == "auto":
            try:
                step["receipt"] = self._execute(action)
                ws.policy.auto_actions_this_turn += 1
                if action.type == "pay_bill" and step["receipt"]["status"] == "executed":
                    ws.policy.auto_paid_today += step["receipt"]["amount_myr"]
            except SandboxError as e:
                step["error"] = str(e)
                ws.audit.record("execution_failed", "sandbox", {"type": action.type, "error": str(e)})
        elif decision.outcome == "needs_approval":
            step["approval_id"] = self._queue(action, decision)
        else:
            alert = {"code": "ACTION_BLOCKED", "type": action.type, "reasons": decision.reasons,
                     "source_ref": action.source_ref}
            ws.alerts_raised.append(alert)
            ws.audit.record("blocked", "policy", alert)
        steps.append(step)
        return step

    # -- routing ----------------------------------------------------------------------
    def _route(self, text: str) -> List[str]:
        t = text.lower()
        found = [name for name, pat in ROUTES if re.search(pat, t)]
        if "autopilot" in found:
            return ["autopilot"]
        if not found and self.ws.llm and self.ws.llm.available:
            found = self.ws.llm.classify(text, INTENTS)
        return found

    def _owner_commands(self, text: str, steps: List[Dict[str, Any]]) -> bool:
        """Direct instructions from the owner. Parameters come from the owner's
        own words (provenance=owner) but still go through policy."""
        t = text.lower()
        acct = guard.ACCOUNT_NUMBER.search(text)
        handled = False
        m = re.search(r"\b(make|get|keep)\b.{0,20}?\b(\d{2}(?:\.\d)?)\s*%\s*(safe|sure|confident|certain)", t) or \
            re.search(r"\b(bullet.?proof|make (me|us|it) safe)\b", t)
        if m:
            pct = float(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) and m.group(2)[0].isdigit() else 95.0
            self._last_facts = {"risk": self._do_confidence_plan(steps, max(0.001, 1 - pct / 100), act=True)}
            return True
        if re.search(r"\b(approve|confirm)\b.*\b(all|everything|pending)\b", t):
            steps.append({"kind": "note", "text": "Approvals can only be given with the Approve button in the "
                                                  "Approvals panel, not through chat."})
            return True
        if re.search(r"\b(raise|increase|change|remove|disable)\b.*\b(limit|policy|rule)s?\b", t):
            self.propose(Action("change_limits", {"request": text[:120]}, "owner",
                                rationale="owner asked to change the agent's own limits"), steps)
            return True
        if re.search(r"\b(new|change|update)\b.*\b(bank|account) (details|number)\b", t) or \
                (acct and re.search(r"\b(pay|transfer|send|remit)\b", t)):
            bill_m = re.search(r"\bBILL-\d+\b", text, re.I)
            self.propose(Action("transfer_external" if not bill_m else "pay_bill",
                                {"bill_id": bill_m.group(0).upper(), "account_number": acct.group(0)} if bill_m and acct
                                else {"account": acct.group(0) if acct else None},
                                "owner", rationale="owner asked to pay a bank account that is not on file"), steps)
            return True
        for m in re.finditer(r"\bpay\b[^.]*?\b(BILL-\d+)\b", text, re.I):
            self.propose(Action("pay_bill", {"bill_id": m.group(1).upper()}, "owner",
                                rationale="owner asked to pay this bill"), steps)
            handled = True
        if re.search(r"\bremind", t):
            for inv in self.ws.company.open_invoices():
                if inv.id.lower() in t or inv.customer.split()[0].lower() in t:
                    self._remind(inv, steps, provenance="owner")
                    handled = True
        m = re.search(r"\bconvert\b[^\d]*([\d,]+(?:\.\d+)?)\s*(usd|us\$|\$)?", t)
        if m:
            self.propose(Action("fx_convert", {"usd": float(m.group(1).replace(",", ""))}, "owner",
                                rationale="owner asked to convert USD to MYR"), steps)
            handled = True
        m = re.search(r"\bapply\b.*\b(invoice financing|invfin|purchase.order financing|po financing|pofin|working.capital|wcline)\b", t)
        if m:
            key = m.group(1).replace("-", " ")
            pid = "INVFIN" if key in ("invoice financing", "invfin") else "WCLINE" if key in ("working capital", "wcline") else "POFIN"
            amt = re.search(r"(?:rm\s*)?([\d,]{4,})", t)
            amount = float(amt.group(1).replace(",", "")) if amt else self._gap() or 50_000
            self.propose(Action("apply_financing", {"product_id": pid, "amount": amount}, "owner",
                                rationale="owner asked to apply"), steps)
            handled = True
        return handled

    # -- read tools (T0) ----------------------------------------------------------------
    def _read(self, name: str, steps, summary: str, data: Any) -> Any:
        self.ws.audit.record("read", "agent", {"tool": name, "summary": summary})
        steps.append({"kind": "read", "tool": name, "summary": summary})
        return data

    def _gap(self) -> float:
        return cashflow.forecast(self.ws.company)["funding_gap"]

    def _do_whatif(self, steps, scenario):
        r = cashflow.whatif(self.ws.company, scenario)
        return self._read("what_if", steps, "; ".join(r["changes"])
                          + f" → gap RM {r['baseline']['funding_gap']:,.0f} → RM {r['whatif']['funding_gap']:,.0f}", r)

    def _do_risk(self, steps):
        return self._do_confidence_plan(steps, 0.05, act=False)

    def _do_confidence_plan(self, steps, target: float, act: bool):
        """Monte Carlo forecast + the cheapest plan to reach the target breach
        probability. With act=True (the owner asked for it) each step of the
        plan is proposed — and the permission policy decides, as always."""
        c = self.ws.company
        sim = risk.simulate(c)
        plan = risk.plan_for_confidence(c, target=target)
        self._read("monte_carlo_forecast", steps,
                   f"{sim['sims']:,} simulated futures: {sim['p_below_floor']:.0%} dip below the floor; "
                   f"gap P50 RM {sim['gap']['p50']:,.0f}, P90 RM {sim['gap']['p90']:,.0f}", sim)
        self._read("confidence_planner", steps,
                   f"target ≤{target:.0%} breach risk: {len(plan['ladder']) - 1} step(s), RM {plan['total_cost']:,.0f} → "
                   f"{plan['p_after']:.1%}; usual plan leaves {plan['classic']['p_below_floor']:.0%}", plan)
        if act:
            for st in plan["ladder"][1:]:
                a = st["action"]
                why = f"confidence plan step {st['step']}: breach risk → {st['p_below_floor']:.1%}"
                ref = a.get("bill_id") or a.get("product_id") or a.get("invoice_id")
                if any(p["action"]["type"] == a["type"] and ref in json.dumps(p["action"]["params"]) for p in self.pending()):
                    steps.append({"kind": "note", "text": f"{st['title']} — already waiting for your approval."})
                    continue
                if a["type"] == "send_corrected_invoice":
                    d = matching.draft_correction(c, a["invoice_id"])
                    if d:
                        self.propose(Action("send_corrected_invoice", {"invoice_id": a["invoice_id"], "to": d["to"],
                                            "subject": d["subject"], "body": d["body"], "new_total": d["new_total"]},
                                            "owner", rationale=why), steps)
                elif a["type"] == "send_reminder":
                    inv = c.invoice(a["invoice_id"])
                    if inv:
                        self._remind(inv, steps, provenance="owner")
                elif a["type"] == "request_supplier_extension":
                    self.propose(Action("request_supplier_extension", {"bill_id": a["bill_id"], "days": a["days"],
                                        "surcharge": a["surcharge"]}, "owner", rationale=why), steps)
                elif a["type"] == "apply_financing":
                    self.propose(Action("apply_financing", {"product_id": a["product_id"], "amount": a["amount"]},
                                        "owner", rationale=why), steps)
        return {"simulation": sim, "plan": plan, "acted": act}

    def _do_forecast(self, steps):
        fc = cashflow.forecast(self.ws.company)
        s = (f"Opening RM {fc['opening_balance']:,.0f}; low point RM {fc['trough']['balance']:,.0f} on "
             f"{fc['trough']['date']}; gap to floor RM {fc['funding_gap']:,.0f}")
        return self._read("cash_forecast", steps, s, fc)

    def _do_health(self, steps):
        a = analysis.analyse(self.ws.company)
        m = a["metrics"]
        s = (f"Gross margin {m['gross_margin']:.0%}, DSO {m['dso_days']}d, DPO {m['dpo_days']}d, "
             f"current ratio {m['current_ratio']}")
        return self._read("business_health", steps, s, a)

    def _do_documents(self, steps):
        c = self.ws.company
        res = [matching.three_way_match(c, i.id) for i in c.open_invoices() if i.order_id and i.lines]
        bad = [r for r in res if r["status"] == "MISMATCH"]
        s = f"{len(res)} invoices matched to PO and delivery note; {len(bad)} mismatched" + (
            f" ({', '.join(r['invoice_id'] for r in bad)})" if bad else "")
        return self._read("three_way_match", steps, s, res)

    def _do_orders(self, steps):
        c = self.ws.company
        bookings.assess_pending(c)
        book = [o for o in c.orders if o.status in ("confirmed", "in_production")]
        pending = [o for o in c.orders if o.status == "pending"]
        rows = [{"id": o.id, "customer": o.customer, "status": o.status, "currency": o.currency, "total": o.total,
                 "deposit_due": o.deposit_due, "delivery": o.delivery_date.isoformat(),
                 "verdict": o.assessment.get("verdict"), "why": o.assessment.get("why")} for o in book + pending]
        s = f"{len(book)} orders in the book, {len(pending)} new booking(s) to assess"
        return self._read("order_book", steps, s, rows)

    def _do_financing(self, steps):
        fc = cashflow.forecast(self.ws.company)
        m = financing.match_for_forecast(self.ws.company, fc)
        if m is None:
            return self._read("match_financing", steps, "No funding gap in the 13-week forecast", None)
        plan = " + ".join(f"{p['name']} RM {p['amount']:,.0f}" for p in m["recommended_plan"]) or "none"
        return self._read("match_financing", steps, f"Need RM {m['need']:,.0f} by {m['need_by']}; plan: {plan}", m)

    def _do_inbox(self, steps):
        scans = list(self.ws.scans.values())
        q = [s for s in scans if s.trust == "quarantined"]
        summary = f"{len(scans)} messages screened; {len(q)} quarantined" + (
            f" ({', '.join(s.message_id for s in q)})" if q else "")
        return self._read("screen_inbox", steps, summary, [s.to_dict() for s in scans])

    def _do_receivables(self, steps):
        c = self.ws.company
        rows = [{"id": i.id, "customer": i.customer, "currency": i.currency, "amount": i.amount,
                 "due": i.due.isoformat(), "days_overdue": max(0, (c.as_of - i.due).days), "terms": i.terms,
                 "financed": i.financed} for i in c.open_invoices()]
        total = sum(to_base(i.amount, i.currency) for i in c.open_invoices())
        return self._read("list_receivables", steps, f"{len(rows)} open invoices, RM {total:,.0f}", rows)

    def _do_bills(self, steps):
        c = self.ws.company
        rows = [{"id": b.id, "payee": c.payee(b.payee_id).name, "amount": b.amount, "due": b.due.isoformat(),
                 "description": b.description} for b in sorted(c.open_bills(), key=lambda b: b.due)]
        total = sum(b.amount for b in c.open_bills())
        return self._read("list_bills", steps, f"{len(rows)} open bills, RM {total:,.0f}", rows)

    # -- autopilot playbook ------------------------------------------------------------
    def _do_autopilot(self, steps):
        c = self.ws.company
        out: Dict[str, Any] = {"inbox": self._do_inbox(steps)}
        for scan in self.ws.scans.values():
            if scan.trust == "quarantined":
                steps.append({"kind": "note", "text": _why_quarantined(scan.to_dict())})

        out["documents"] = self._do_documents(steps)
        for r in out["documents"]:
            draft = matching.draft_correction(c, r["invoice_id"]) if r["status"] == "MISMATCH" else None
            if draft:
                self.propose(Action("send_corrected_invoice",
                                    {"invoice_id": r["invoice_id"], "to": draft["to"], "subject": draft["subject"],
                                     "body": draft["body"], "new_total": draft["new_total"]}, "books",
                                    rationale=f"{r['discrepancies'][0]['label']} doesn't match {r['po_number']} — "
                                              f"the customer would hold it ~{r['expected_delay_days']} days"),
                             steps)

        out["orders"] = self._do_orders(steps)
        for o in [o for o in c.orders if o.status == "pending"]:
            ax = o.assessment
            if ax.get("verdict") == "decline":
                self.propose(Action("decline_booking", {"order_id": o.id, "reason": ax["why"]}, "books",
                                    source_ref=None, rationale=f"booking {o.id}: {ax['why']}"), steps)
            else:
                self.propose(Action("confirm_booking", {"order_id": o.id, "deposit_pct": ax["deposit_pct"],
                                                        "to": o.contact, "body": _confirmation(c, o, ax)}, "books",
                                    rationale=f"{o.customer} {o.po_number}: {ax['why']}; "
                                              f"{ax['deposit_pct']:.0%} deposit, ready by {_d(ax['ready_by']) if ax['ready_by'] else '—'}"),
                             steps)
        for o in c.orders:
            if o.status in ("confirmed", "in_production") and o.deposit_due > 0 and not o.assessment.get("deposit_requested"):
                to = c.customer_contacts.get(o.customer)
                self.propose(Action("request_deposit", {"order_id": o.id, "to": to,
                                                        "subject": f"Deposit for {o.po_number}",
                                                        "body": _deposit_note(c, o)}, "books",
                                    rationale=f"{o.currency} {o.deposit_due:,.0f} deposit outstanding on {o.id}"), steps)

        out["forecast"] = self._do_forecast(steps)
        for inv in c.open_invoices():
            if inv.due < c.as_of and not inv.financed:
                self._remind(inv, steps, provenance="books")

        for bill in sorted(c.open_bills(), key=lambda b: b.due):
            if (bill.due - c.as_of).days <= 5:
                self.propose(Action("pay_bill", {"bill_id": bill.id}, "books",
                                    rationale=f"due {bill.due.isoformat()}"), steps)

        out["financing"] = self._do_financing(steps)
        m = out["financing"]
        if m:
            for p in m["recommended_plan"]:
                if p["id"] == "SUPTERMS":
                    bill = financing.supplier_extension_candidate(c)[0]
                    self.propose(Action("request_supplier_extension", {"bill_id": bill.id, "days": 30, "surcharge": 0.015},
                                        "books", rationale=f"cheapest on-time option: moves RM {bill.amount:,.0f} past the gap "
                                                           f"for a 1.5% surcharge (~RM {bill.amount * 0.015:,.0f})"), steps)
                else:
                    self.propose(Action("apply_financing", {"product_id": p["id"], "amount": p["amount"]}, "books",
                                        rationale=f"{p['name']}: funds by {p['funds_by']}, est. cost RM {p['cost']:,.0f}"),
                                 steps)
            if matching.open_mismatches(c):
                steps.append({"kind": "note", "text": "Once the customer approves the corrected invoice, it can be "
                                                      "financed too — and it lands on time, closing most of the gap."})
            plan = risk.plan_for_confidence(c, target=0.05)
            if plan["classic"]["p_below_floor"] > 0.10 and plan["reached"]:
                out["risk_hint"] = plan
                steps.append({"kind": "note", "text":
                              f"This plan closes the gap in the single forecast, but in {plan['sims']:,} simulated futures "
                              f"{plan['classic']['p_below_floor']:.0%} still dip below your floor. For RM "
                              f"{max(0, plan['total_cost'] - plan['classic']['cost']):,.0f} more, the risk drops to "
                              f"{plan['p_after']:.0%} — say “make me 95% safe”."})
        return out

    def _remind(self, inv, steps, provenance: str):
        c = self.ws.company
        to = c.customer_contacts.get(inv.customer)
        overdue = (c.as_of - inv.due).days
        body = (f"Dear {inv.customer} accounts team,\n\nOur records show invoice {inv.id} for "
                f"{inv.currency} {inv.amount:,.2f} was due on {inv.due.isoformat()} ({overdue} days ago). "
                f"Could you confirm the payment date?\n\nRegards,\n{c.profile.owner}\n{c.profile.legal_name}")
        self.propose(Action("send_reminder", {"invoice_id": inv.id, "to": to,
                                              "subject": f"Payment reminder — {inv.id}", "body": body},
                            provenance, rationale=f"{overdue} days overdue"), steps)

    # -- execution & approvals ---------------------------------------------------------
    def _execute(self, a: Action) -> Dict[str, Any]:
        ws, sb, p = self.ws, self.ws.sandbox, a.params
        key = _key(a, ws.company.as_of)
        if a.type == "pay_bill":
            r = sb.pay_bill(p["bill_id"], key)
        elif a.type == "fx_convert":
            r = sb.fx_convert(float(p["usd"]), key)
        elif a.type == "send_reminder":
            r = sb.send_message("reminder", p["to"], p["subject"], p["body"], key, ref=p["invoice_id"])
            ws.policy.reminder_log[p["invoice_id"]] = ws.company.as_of
        elif a.type == "send_corrected_invoice":
            r = sb.issue_corrected_invoice(p["invoice_id"], p["to"], p["subject"], p["body"], key)
        elif a.type == "request_deposit":
            r = sb.send_message("deposit_request", p["to"], p["subject"], p["body"], key, ref=p["order_id"])
            ws.company.order(p["order_id"]).assessment["deposit_requested"] = ws.company.as_of.isoformat()
        elif a.type == "confirm_booking":
            r = sb.confirm_booking(p["order_id"], float(p["deposit_pct"]), p["to"], p["body"], key)
        elif a.type == "decline_booking":
            r = sb.decline_booking(p["order_id"], p.get("reason", ""), key)
        elif a.type == "request_supplier_extension":
            r = sb.extend_supplier_terms(p["bill_id"], int(p.get("days", 30)), float(p.get("surcharge", 0.015)), key)
        elif a.type == "apply_financing":
            r = sb.submit_financing(p["product_id"], float(p["amount"]), key)
        else:
            raise SandboxError(f"no executor for {a.type}")
        ws.audit.record("executed", "sandbox", {"type": a.type, "ref": r["ref"], "status": r["status"]})
        return r

    def _queue(self, a: Action, decision) -> str:
        aid = "APR-" + uuid.uuid4().hex[:8].upper()
        amount = self._amount_of(a)
        self.ws.approvals[aid] = {
            "id": aid, "status": "pending", "created_at": time.time(),
            "expires_at": time.time() + self.ws.policy.limits.approval_ttl_hours * 3600,
            "action": {"type": a.type, "params": a.params, "provenance": a.provenance,
                       "source_ref": a.source_ref, "rationale": a.rationale},
            "decision": decision.to_dict(), "amount_myr": amount, "title": self._title(a),
        }
        self.ws.audit.record("queued_for_approval", "agent", {"approval_id": aid, "type": a.type})
        return aid

    def _amount_of(self, a: Action) -> float:
        c, p = self.ws.company, a.params
        if a.type in ("pay_bill", "request_supplier_extension") and c.bill(p.get("bill_id", "")):
            b = c.bill(p["bill_id"])
            return to_base(b.amount, b.currency)
        if a.type == "apply_financing":
            return float(p.get("amount", 0))
        if a.type == "confirm_booking" and c.order(p.get("order_id", "")):
            o = c.order(p["order_id"])
            return to_base(o.total, o.currency)
        if a.type == "fx_convert":
            return to_base(float(p.get("usd", 0)), "USD")
        return 0.0

    def _title(self, a: Action) -> str:
        c, p = self.ws.company, a.params
        if a.type == "pay_bill" and c.bill(p["bill_id"]):
            b = c.bill(p["bill_id"])
            return f"Pay {c.payee(b.payee_id).name} RM {b.amount:,.0f} ({b.id})"
        if a.type == "request_supplier_extension":
            b = c.bill(p["bill_id"])
            return f"Ask {c.payee(b.payee_id).name} to move {b.id} (RM {b.amount:,.0f}) by {p.get('days', 30)} days"
        if a.type == "apply_financing":
            name = next((x.name for x in financing.CATALOGUE if x.id == p["product_id"]), p["product_id"])
            return f"Apply for {name}: RM {float(p['amount']):,.0f}"
        if a.type == "fx_convert":
            return f"Convert USD {float(p['usd']):,.0f} to MYR"
        if a.type == "send_reminder":
            return f"Send payment reminder for {p.get('invoice_id')} to {p.get('to')}"
        if a.type == "send_corrected_invoice":
            return f"Send corrected invoice {p.get('invoice_id')} to {p.get('to')}"
        if a.type == "confirm_booking":
            o = c.order(p["order_id"])
            return f"Confirm booking {o.id} from {o.customer} ({o.currency} {o.total:,.0f}, {float(p['deposit_pct']):.0%} deposit)"
        if a.type == "decline_booking":
            return f"Decline booking {p.get('order_id')}"
        if a.type == "request_deposit":
            return f"Request deposit for {p.get('order_id')}"
        return a.type

    # -- replies ------------------------------------------------------------------------
    @staticmethod
    def _step_brief(s: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": s["type"], "rationale": s.get("rationale"), "outcome": s["decision"]["outcome"],
                "tier": s["decision"]["tier_label"], "reasons": s["decision"]["reasons"],
                "receipt": (s.get("receipt") or {}).get("ref")}

    @staticmethod
    def _facts_for_llm(facts: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        fc = facts.get("forecast") or (facts.get("autopilot") or {}).get("forecast")
        if fc:
            out["forecast"] = {k: fc[k] for k in ("opening_balance", "trough", "funding_gap", "first_buffer_breach", "cash_buffer")}
            out["alerts"] = [a["title"] for a in fc["alerts"]]
        fin = facts.get("financing") or (facts.get("autopilot") or {}).get("financing")
        if fin:
            out["financing_plan"] = fin["recommended_plan"]
        if facts.get("health"):
            out["health"] = facts["health"]["metrics"]
        rk = facts.get("risk")
        if rk:
            sim, plan = rk["simulation"], rk["plan"]
            out["risk"] = {"simulated_futures": sim["sims"], "p_below_floor_now": f"{sim['p_below_floor']:.0%}",
                           "gap_p50": sim["gap"]["p50"], "gap_p90": sim["gap"]["p90"],
                           "single_forecast_gap": sim["deterministic"]["funding_gap"],
                           "usual_plan": plan["classic"], "confidence_plan_steps": [
                               {"title": x["title"], "cost": x["cost"], "p_below_floor_after": f"{x['p_below_floor']:.1%}"}
                               for x in plan["ladder"][1:]],
                           "confidence_plan_cost": plan["total_cost"], "p_below_floor_after": f"{plan['p_after']:.1%}",
                           "target_safety": f"{1 - plan['target']:.0%}", "proposed_for_approval": rk["acted"]}
            out["risk"]["usual_plan"] = {**plan["classic"], "p_below_floor": f"{plan['classic']['p_below_floor']:.0%}"}
        return out

    def _template_reply(self, intents, facts, steps) -> str:
        out: List[str] = []

        def section(title: str, items: List[str]) -> None:
            if items:
                out.append(f"{title}\n" + "\n".join(f"• {i}" for i in items))

        wi = facts.get("whatif")
        if wi:
            b, w = wi["baseline"], wi["whatif"]
            out.append(L("whatif_head", changes=", ".join(wi["changes"]), b=_rm(b["trough"]["balance"]),
                         bd=_d(b["trough"]["date"]), w=_rm(w["trough"]["balance"]), wd=_d(w["trough"]["date"])))
            items = [L("gap_line", a=_rm(b["funding_gap"]), b=_rm(w["funding_gap"])),
                     L("close_line", a=_rm(b["closing"]), b=_rm(w["closing"]))]
            if w["first_buffer_breach"] and w["first_buffer_breach"] != b["first_buffer_breach"]:
                items.append(L("first_floor", d=_d(w["first_buffer_breach"])))
            section(L("impact"), items)
            out.append(L("sim_only"))

        rk = facts.get("risk")
        if rk:
            sim, plan = rk["simulation"], rk["plan"]
            if sim["deterministic"]["funding_gap"] > 0:
                out.append(L("risk_head", n=f"{sim['sims']:,}", p=f"{sim['p_below_floor']:.0%}",
                             det=_rm(sim["deterministic"]["funding_gap"]), p50=_rm(sim["gap"]["p50"]),
                             p90=_rm(sim["gap"]["p90"])))
                cl = plan["classic"]
                out.append(L("risk_classic", cost=_rm(cl["cost"]), p=f"{cl['p_below_floor']:.0%}"))
            else:
                out.append(L("risk_head_nogap", n=f"{sim['sims']:,}", p=f"{sim['p_below_floor']:.0%}",
                             p90=_rm(sim["gap"]["p90"])))
            section(L("risk_plan_title", target=f"{1 - plan['target']:.0%}", cost=_rm(plan["total_cost"]),
                      p=f"{plan['p_after']:.1%}"),
                    [f"{x['title']} — {_rm(x['cost'])} → {x['p_below_floor']:.1%}" for x in plan["ladder"][1:]])
            if not plan["reached"]:
                out.append(L("risk_not_reached", p=f"{plan['p_after']:.1%}"))
            out.append(L("risk_acted") if rk["acted"] else L("risk_offer"))

        fc = facts.get("forecast") or (facts.get("autopilot") or {}).get("forecast")
        if fc:
            if fc["funding_gap"] > 0:
                out.append(L("cash_dips", low=_rm(fc["trough"]["balance"]), d=_d(fc["trough"]["date"]),
                             gap=_rm(fc["funding_gap"]), floor=_rm(fc["cash_buffer"])))
            else:
                out.append(L("cash_ok", floor=_rm(fc["cash_buffer"]), low=_rm(fc["trough"]["balance"]), d=_d(fc["trough"]["date"])))
            section(L("why"), [a["title"] for a in fc["alerts"]
                            if a["code"] in ("INVOICE_MISMATCH", "INVOICE_CORRECTION_PENDING", "DOUBTFUL_RECEIVABLE")])
        if facts.get("health"):
            section(L("health"), [i["title"] for i in facts["health"]["insights"]])
        docs = facts.get("documents")
        if docs:
            items = [f"{r['invoice_id']} vs {r['po_number']}: {d['label']} — {d['detail']}"
                     for r in docs if r["status"] == "MISMATCH" for d in r["discrepancies"]]
            if items:
                section(L("mismatch"), items)
            else:
                out.append(L("all_match"))
        if facts.get("orders") is not None and "autopilot" not in intents:
            rows = facts["orders"]
            section(L("orders"), [f"{r['id']} {r['customer']} — {r['status']}"
                                               + (f", deposit {r['currency']} {r['deposit_due']:,.0f} due" if r['deposit_due'] else "")
                                               + (f" · Tidewise suggests: {r['verdict']} ({r['why']})" if r['status'] == 'pending' else "")
                                               for r in rows])
        fin = facts.get("financing")
        if fin and "autopilot" not in intents:
            section(L("fin_title", need=_rm(fin["need"]), d=_d(fin["need_by"])),
                    [f"{p['name']}: {_rm(p['amount'])}, est. cost {_rm(p['cost'])}, funds by {_d(p['funds_by'])}"
                     for p in fin["recommended_plan"]])
        elif "financing" in facts and fin is None:
            out.append(L("no_gap"))
        if facts.get("inbox") and "autopilot" not in intents:
            q = [m for m in facts["inbox"] if m["trust"] == "quarantined"]
            if q:
                section(L("quarantined"), [_why_quarantined(m) for m in q])
            else:
                out.append(L("inbox_clean"))
        if facts.get("receivables"):
            od = [r for r in facts["receivables"] if r["days_overdue"]]
            section(L("receivables", n=len(facts["receivables"]), m=len(od)),
                    [f"{r['id']} {r['customer']} — {r['currency']} {r['amount']:,.0f}, {r['days_overdue']} days late" for r in od])
        if facts.get("bills"):
            section(L("next_bills"), [f"{b['id']} {b['payee']} — {_rm(b['amount'])} due {_d(b['due'])}" for b in facts["bills"][:5]])

        actions = [s for s in steps if s["kind"] == "action"]
        section(L("done"), [self._label(s) for s in actions if s["decision"]["outcome"] == "auto" and s.get("receipt")])
        section(L("waiting"), [self._label(s) for s in actions if s["decision"]["outcome"] == "needs_approval"])
        section(L("refused"), [f"{self._label(s)} — {s['decision']['reasons'][0]}" for s in actions
                            if s["decision"]["outcome"] == "blocked"])
        section(L("heads_up"), [s["text"] for s in steps if s["kind"] == "note"])
        return "\n\n".join(out)

    def _label(self, s: Dict[str, Any]) -> str:
        c, p = self.ws.company, s["params"]
        done = s["decision"]["outcome"] == "auto" and s.get("receipt")
        if s["type"] == "pay_bill" and c.bill(p.get("bill_id", "")):
            b = c.bill(p["bill_id"])
            return f"{'paid' if done else 'pay'} {c.payee(b.payee_id).name} {_rm(b.amount)} ({b.id})"
        if s["type"] == "request_supplier_extension" and c.bill(p.get("bill_id", "")):
            b = c.bill(p["bill_id"])
            return f"ask {c.payee(b.payee_id).name} to move {b.id} ({_rm(b.amount)}) by {p.get('days', 30)} days"
        if s["type"] == "apply_financing":
            name = next((x.name for x in financing.CATALOGUE if x.id == p.get("product_id")), p.get("product_id"))
            return f"apply for {name}: {_rm(float(p.get('amount', 0)))}"
        return {
            "send_reminder": lambda: f"{'reminded' if done else 'remind'} {p.get('to')} about {p.get('invoice_id')}",
            "send_corrected_invoice": lambda: f"{'sent' if done else 'send'} corrected invoice {p.get('invoice_id')} to {p.get('to')}",
            "request_deposit": lambda: f"{'requested' if done else 'request'} the deposit for {p.get('order_id')}",
            "confirm_booking": lambda: f"{'confirmed' if done else 'confirm'} booking {p.get('order_id')} ({float(p.get('deposit_pct', 0)):.0%} deposit)",
            "decline_booking": lambda: f"{'declined' if done else 'decline'} booking {p.get('order_id')} — {p.get('reason')}",
            "fx_convert": lambda: f"convert USD {float(p.get('usd', 0)):,.0f}",
            "pay_bill": lambda: f"pay {p.get('bill_id')}",
        }.get(s["type"], lambda: s["type"].replace("_", " "))()
