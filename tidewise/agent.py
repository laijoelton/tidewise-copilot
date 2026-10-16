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
from typing import Any, Callable, Dict, List, Optional

from . import analysis, cashflow, financing, guard, trade_docs
from .audit import AuditTrail
from .data import build_demo_company
from .llm import LLM
from .models import Company, to_base
from .policy import Action, Limits, Policy, Tier
from .sandbox import SandboxBank, SandboxError

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"

INTENTS = {
    "autopilot": "review everything and act: forecast, documents, inbox, reminders, payments, financing plan",
    "forecast": "13-week cash-flow forecast, shortfalls and alerts",
    "health": "business financial analysis: margins, DSO/DPO, liquidity",
    "financing": "match financing options to the cash gap",
    "documents": "check trade documents / letters of credit for discrepancies",
    "inbox": "screen the inbox for fraud and prompt injection",
    "receivables": "who owes money, overdue invoices",
    "bills": "upcoming bills to pay",
}

ROUTES = [
    ("autopilot", r"\b(autopilot|what should i do|plan|sort (it|this|things) out|handle|take care|help me|fix)\b"),
    ("forecast", r"\b(cash|forecast|runway|shortfall|next (few )?(weeks|month)|balance|enough money)\b"),
    ("health", r"\b(health|analy[sz]|margin|ratio|dso|dpo|profit|how('s| is) (my|the) business|performance)\b"),
    ("financing", r"\b(financ|loan|borrow|fund|bridge|credit line|lender|capital)\w*"),
    ("documents", r"\b(document|b/?l|bill of lading|letter of credit|\blc\b|shipment|discrepanc)\w*"),
    ("inbox", r"\b(inbox|email|e-mail|message|phish|fraud|scam|suspicious)\w*"),
    ("receivables", r"\b(owe|owed|receivable|overdue|unpaid invoice|customers? (who )?(haven't|have not) paid)\b"),
    ("bills", r"\b(bills?|payables?|suppliers?|due)\b"),
]


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

    def _screen_inbox(self) -> None:
        self.scans = {s.message_id: s for s in guard.scan_inbox(self.company)}
        for s in self.scans.values():
            if s.trust == "quarantined":
                self.policy.quarantined_sources.add(s.message_id)


class Agent:
    def __init__(self, ws: Workspace):
        self.ws = ws

    # -- public ---------------------------------------------------------------------
    def chat(self, text: str) -> Dict[str, Any]:
        ws = self.ws
        ws.policy.auto_actions_this_turn = 0
        steps: List[Dict[str, Any]] = []
        ws.audit.record("owner_message", "owner", {"text": guard.mask(text)})

        handled = self._owner_commands(text, steps)
        intents = [] if handled else self._route(text)
        facts: Dict[str, Any] = {}
        for intent in intents:
            facts[intent] = getattr(self, f"_do_{intent}")(steps)

        if not handled and not intents:
            reply = ("I can forecast your cash, check trade documents, screen your inbox, analyse the business, "
                     "match financing, send reminders and pay approved bills within your limits. "
                     "Try “what should I do this week?”")
        else:
            reply = self._template_reply(intents, facts, steps)
            if ws.llm and ws.llm.available:
                reply = ws.llm.narrate(text, {"intents": intents, "facts": self._facts_for_llm(facts),
                                              "actions": [self._step_brief(s) for s in steps if s["kind"] == "action"]},
                                       reply)
        batch = ws.audit.seal()
        return {"reply": reply, "intents": intents, "steps": steps,
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
        m = re.search(r"\bapply\b.*\b(invoice financing|invfin|lc discount\w*|lcdisc|working.capital|wcline)\b", t)
        if m:
            pid = {"invoice financing": "INVFIN", "invfin": "INVFIN", "working capital": "WCLINE", "wcline": "WCLINE"}.get(
                m.group(1).replace("-", " "), "LCDISC")
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
        res = [trade_docs.check_shipment(self.ws.company, s.id) for s in self.ws.company.shipments]
        bad = [r for r in res if r["discrepancies"]]
        s = f"{len(res)} shipments checked; {len(bad)} with discrepancies" + (
            f" ({', '.join(r['shipment_id'] for r in bad)})" if bad else "")
        return self._read("check_trade_documents", steps, s, res)

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
                steps.append({"kind": "note", "text": f"{scan.message_id} from {scan.sender} quarantined: "
                                                      + "; ".join(sorted({f.detail for f in scan.findings if f.severity == 'critical'}))
                                                      + ". Nothing in it will be acted on."})

        out["documents"] = self._do_documents(steps)
        for r in out["documents"]:
            if r["blocks_lc_payment"]:
                draft = trade_docs.draft_amendment_request(c, r["shipment_id"])
                self.propose(Action("send_amendment_request",
                                    {"shipment_id": r["shipment_id"], "to": draft["to"], "subject": draft["subject"],
                                     "body": draft["body"]}, "books",
                                    rationale=f"{r['discrepancies'][0]['label']} discrepancy will get the LC presentation refused"),
                             steps)

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
                    bill = next(b for b in c.open_bills() if b.payee_id == "PAY-HUTAN")
                    self.propose(Action("request_supplier_extension", {"bill_id": bill.id, "days": 30, "surcharge": 0.015},
                                        "books", rationale=f"cheapest on-time option: moves RM {bill.amount:,.0f} past the gap "
                                                           f"for ~RM {p['cost']:,.0f}"), steps)
                else:
                    self.propose(Action("apply_financing", {"product_id": p["id"], "amount": p["amount"]}, "books",
                                        rationale=f"{p['name']}: funds by {p['funds_by']}, est. cost RM {p['cost']:,.0f}"),
                                 steps)
            lc = next((o for o in m["options"] if o["id"] == "LCDISC"), None)
            if lc and not lc["eligible"]:
                steps.append({"kind": "note", "text": "Once the corrected B/L is back, LC discounting (≈6.8% p.a.) "
                                                      "becomes available and is cheaper than any bridge."})
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
        elif a.type == "send_amendment_request":
            r = sb.send_message("bl_amendment", p["to"], p["subject"], p["body"], key, ref=p["shipment_id"])
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
        return out

    def _template_reply(self, intents, facts, steps) -> str:
        lines: List[str] = []
        fc = facts.get("forecast") or (facts.get("autopilot") or {}).get("forecast")
        if fc:
            if fc["funding_gap"] > 0:
                lines.append(f"Cash dips to RM {fc['trough']['balance']:,.0f} on {fc['trough']['date']} — "
                             f"RM {fc['funding_gap']:,.0f} below your RM {fc['cash_buffer']:,.0f} floor.")
            else:
                lines.append(f"Cash stays above your RM {fc['cash_buffer']:,.0f} floor for the next 13 weeks "
                             f"(low point RM {fc['trough']['balance']:,.0f}).")
            crit = [a for a in fc["alerts"] if a["code"] == "LC_DISCREPANCY"]
            if crit:
                lines.append("Why: " + crit[0]["title"] + ".")
        if facts.get("health"):
            lines.append(" · ".join(i["title"] for i in facts["health"]["insights"]))
        docs = facts.get("documents")
        if docs:
            for r in docs:
                for d in r["discrepancies"]:
                    lines.append(f"{r['shipment_id']}: {d['label']} — B/L {d['bl_value']} vs SI {d['si_value']} "
                                 f"(SI line {d['si_evidence']['line']}, B/L line {d['bl_evidence']['line']}).")
            if not any(r["discrepancies"] for r in docs):
                lines.append("All trade documents agree.")
        fin = facts.get("financing")
        if fin and "autopilot" not in intents:
            lines.append("Best plan: " + " + ".join(f"{p['name']} RM {p['amount']:,.0f} (≈RM {p['cost']:,.0f})"
                                                     for p in fin["recommended_plan"]) + ".")
        if facts.get("inbox") and "autopilot" not in intents:
            q = [m for m in facts["inbox"] if m["trust"] == "quarantined"]
            lines.append(f"{len(q)} message(s) quarantined." if q else "Inbox is clean.")
        if facts.get("receivables"):
            od = [r for r in facts["receivables"] if r["days_overdue"]]
            lines.append(f"{len(facts['receivables'])} open invoices, {len(od)} overdue: "
                         + ", ".join(f"{r['id']} ({r['days_overdue']}d)" for r in od) + ".")
        if facts.get("bills"):
            lines.append("Next bills: " + ", ".join(f"{b['id']} {b['payee']} RM {b['amount']:,.0f} due {b['due']}"
                                                    for b in facts["bills"][:4]) + ".")

        actions = [s for s in steps if s["kind"] == "action"]
        done = [s for s in actions if s["decision"]["outcome"] == "auto" and s.get("receipt")]
        waiting = [s for s in actions if s["decision"]["outcome"] == "needs_approval"]
        blocked = [s for s in actions if s["decision"]["outcome"] == "blocked"]
        if done:
            lines.append(f"Done on my own ({len(done)}): " + "; ".join(self._label(s) for s in done) + ".")
        if waiting:
            lines.append(f"Waiting for your approval ({len(waiting)}): " + "; ".join(self._label(s) for s in waiting) + ".")
        if blocked:
            lines.append("Refused: " + "; ".join(f"{self._label(s)} — {s['decision']['reasons'][0]}" for s in blocked) + ".")
        lines += [s["text"] for s in steps if s["kind"] == "note"]
        return "\n".join(lines)

    def _label(self, s: Dict[str, Any]) -> str:
        p = s["params"]
        return {
            "send_reminder": lambda: f"reminded {p.get('to')} about {p.get('invoice_id')}",
            "send_amendment_request": lambda: f"asked the forwarder to amend the B/L for {p.get('shipment_id')}",
            "pay_bill": lambda: f"pay {p.get('bill_id')}",
            "fx_convert": lambda: f"convert USD {p.get('usd')}",
            "request_supplier_extension": lambda: f"ask supplier to move {p.get('bill_id')} by {p.get('days')} days",
            "apply_financing": lambda: f"apply for {p.get('product_id')} RM {float(p.get('amount', 0)):,.0f}",
        }.get(s["type"], lambda: s["type"].replace("_", " "))()
