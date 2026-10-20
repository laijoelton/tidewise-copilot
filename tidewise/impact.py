"""What Tidewise has done for the owner, in money, risk and time.

Every figure is computed from the session's own records: the books before
(a fresh copy of the demo company) versus now, the sandbox's operation log,
outbox and financing applications, the inbox screening and the audit trail.
The only estimate is time saved, which uses stated minutes per task and is
labelled as an estimate.
"""

import re
from functools import lru_cache
from typing import Any, Dict, List

from . import cashflow, financing, risk
from .data import build_demo_company
from .matching import HOLD_DAYS_CORRECTION_SENT, HOLD_DAYS_UNRESOLVED
from .models import to_base

# Minutes an owner would spend doing each task by hand (estimate, shown as such).
MINUTES = {"corrected_invoice": 45, "reminder": 10, "deposit_request": 10, "booking_confirmation": 15,
           "booking_declined": 15, "bill_paid": 5, "invoice_checked": 15, "email_screened": 3}
AMOUNT = re.compile(r"\b(RM|MYR|USD|US\$)\s?([\d,]+(?:\.\d+)?)", re.I)


@lru_cache(maxsize=1)
def _baseline() -> Dict[str, Any]:
    """The demo business before Tidewise did anything (deterministic)."""
    c = build_demo_company()
    fc = cashflow.forecast(c)
    sim = risk.simulate(c)
    # The fast option the owner would grab in a panic, priced exactly as the
    # Financing page prices it (same need, need-by date and tenor).
    m = financing.match_for_forecast(c, fc) or {"options": []}
    alt = next((o for o in m["options"] if o.get("high_cost") and o.get("on_time")), None)
    fast = {"name": alt["name"], "cost": alt["cost"], "apr": alt["effective_apr"], "amount": m["need"]} if alt else None
    return {"gap": fc["funding_gap"], "p_below_floor": sim["p_below_floor"], "gap_p90": sim["gap"]["p90"],
            "bills": {b.id: b.amount for b in c.bills}, "fast_cash": fast}


def _largest_amount_myr(text: str) -> float:
    best = 0.0
    for cur, num in AMOUNT.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        best = max(best, to_base(v, "USD") if cur.upper().startswith("US") else v)
    return best


def impact(ws) -> Dict[str, Any]:
    base = _baseline()
    c, sb = ws.company, ws.sandbox
    fc = cashflow.forecast(c)
    sim = risk.simulate(c)

    # -- money ------------------------------------------------------------------------
    surcharges = sum(max(0.0, b.amount - base["bills"].get(b.id, b.amount)) for b in c.bills)
    fin_cost = 0.0
    for app in sb.applications:
        repaid = sum(r["amount_myr"] for r in c.repayments if r["source"] == app["product_id"])
        fin_cost += max(0.0, repaid - app["disbursed"])
    spent = round(surcharges + fin_cost, 2)
    acted = bool(sb.log)
    gap_closed = round(max(0.0, base["gap"] - fc["funding_gap"]), 2)
    fast = base["fast_cash"]

    corrected = [i for i in c.invoices if i.correction in ("sent", "accepted")]
    sooner = [{"invoice": i.id, "customer": i.customer, "currency": i.currency, "amount": i.amount,
               "days_sooner": HOLD_DAYS_UNRESOLVED - (HOLD_DAYS_CORRECTION_SENT if i.correction == "sent" else 0)}
              for i in corrected]

    # -- fraud and attacks --------------------------------------------------------------
    blocked: List[Dict[str, Any]] = []
    declined: List[Dict[str, Any]] = []
    msgs = {m.id: m for m in c.inbox}
    for s in ws.scans.values():
        if s.trust == "quarantined" and s.message_id in msgs:
            m = msgs[s.message_id]
            amt = _largest_amount_myr(m.subject + " " + m.body)
            if amt:
                blocked.append({"what": f"{m.subject[:60]} ({m.sender})", "amount_myr": round(amt, 2)})
    for o in c.orders:
        if o.status == "declined":
            declined.append({"what": f"Suspicious booking {o.id} from {o.customer}", "currency": o.currency,
                             "amount": o.total, "amount_myr": round(to_base(o.total, o.currency), 2)})
    lab = [r for r in getattr(ws.audit, "records", []) if isinstance(r, dict) and r.get("kind") == "attack_lab"]

    # -- work done ----------------------------------------------------------------------
    kinds = [o["kind"] for o in sb.outbox]
    tasks = {
        "corrected_invoice": kinds.count("corrected_invoice"),
        "reminder": kinds.count("reminder"),
        "deposit_request": kinds.count("deposit_request"),
        "booking_confirmation": kinds.count("booking_confirmation"),
        "booking_declined": sum(1 for o in c.orders if o.status == "declined"),
        "bill_paid": sum(1 for r in sb.log if r.get("op") == "pay_bill" and r.get("status") == "executed"),
        "invoice_checked": sum(1 for i in c.invoices if i.order_id and i.lines) if acted else 0,
        "email_screened": len(ws.scans) if acted else 0,
    }
    minutes = sum(MINUTES[k] * n for k, n in tasks.items())

    return {
        "acted": acted,
        "cash": {"gap_before": base["gap"], "gap_now": fc["funding_gap"], "gap_closed": gap_closed,
                 "risk_before": base["p_below_floor"], "risk_now": sim["p_below_floor"],
                 "bad_case_before": base["gap_p90"], "bad_case_now": sim["gap"]["p90"]},
        "cost": {"spent": spent, "surcharges": round(surcharges, 2), "financing": round(fin_cost, 2),
                 "fast_cash": fast,
                 "saved_vs_fast_cash": round(fast["cost"] - spent, 2) if (acted and fast) else None},
        "receivables_sooner": sooner,
        "fraud": {"blocked_myr": round(sum(b["amount_myr"] for b in blocked), 2), "items": blocked,
                  "risky_orders_declined": declined, "attack_lab_attempts": len(lab)},
        "work": {"tasks": tasks, "actions": sum(tasks.values()), "hours_saved_estimate": round(minutes / 60, 1),
                 "minutes_per_task": MINUTES},
    }
