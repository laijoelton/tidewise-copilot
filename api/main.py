"""Tidewise API — FastAPI over one in-memory workspace.

Run from the repo root:  uvicorn api.main:app --reload --port 8000

Two channels, deliberately separate:
  * the conversation (/api/chat) can read, propose and run actions the
    policy allows autonomously — it can never approve anything;
  * approvals (/api/approvals/*) require the owner token header, so a
    prompt-injected agent, a malicious page or a stolen chat session cannot
    release a payment.
"""

import hmac
import os
import secrets
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from tidewise.engine.ratelimit import RateLimiter  # noqa: E402
from tidewise import analysis, arena, bookings, cashflow, financing, guard, market, matching, redteam, risk  # noqa: E402
from datetime import date as _date  # noqa: E402
from tidewise.agent import Workspace  # noqa: E402
from tidewise.policy import BASE_TIER, TIER_LABEL  # noqa: E402

OWNER_TOKEN = os.environ.get("TIDEWISE_OWNER_TOKEN") or "demo-owner-" + secrets.token_hex(4)
ALLOWED_ORIGINS = os.environ.get("TIDEWISE_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app = FastAPI(title="Tidewise API", version="0.1")
market.start_background_refresh()   # live BNM / CFETS data, fetched off the request path
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", "X-Owner-Token"])

ws = Workspace(persist=True)
_limiter = RateLimiter(window_s=1.0)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(f"api:{ip}", 60):
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
    return await call_next(request)


def require_owner(x_owner_token: Optional[str] = Header(default=None)):
    if not x_owner_token or not hmac.compare_digest(x_owner_token, OWNER_TOKEN):
        raise HTTPException(status_code=401, detail="owner token required for approvals")
    return "owner"


@app.on_event("startup")
def _announce():
    print(f"\n  Tidewise owner token (approvals channel): {OWNER_TOKEN}\n")

    def warm():                       # train the ML models off the request path
        from tidewise import ml
        ml.threat_model(), ml.collection_model()
    import threading
    threading.Thread(target=warm, daemon=True).start()


# -- models --------------------------------------------------------------------------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    lang: Optional[str] = Field(default=None, pattern="^(en|zh|ms)$")   # None = detect from the message


class ApproveIn(BaseModel):
    confirm_amount: Optional[float] = None


class WhatIfIn(BaseModel):
    customer_delay: Optional[dict] = None
    lose_customer: Optional[str] = Field(default=None, max_length=80)
    sales_change_pct: Optional[float] = Field(default=None, ge=-100, le=200)
    extra_monthly_cost: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    usd_change_pct: Optional[float] = Field(default=None, ge=-50, le=50)


class AutonomyIn(BaseModel):
    paused: bool


class InvoiceRef(BaseModel):
    invoice_id: str = Field(pattern=r"^INV-\d{4}$")


class OrderRef(BaseModel):
    order_id: str = Field(pattern=r"^ORD-\d{3}$")


class BookingItem(BaseModel):
    sku: str = Field(pattern=r"^[A-Z]{2}-[A-Z]{3,6}$")
    qty: int = Field(ge=1, le=5000)


class BookingIn(BaseModel):
    customer: str = Field(min_length=2, max_length=80)
    email: str = Field(pattern=r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,120}\.[A-Za-z]{2,24}$")
    po_number: str = Field(min_length=1, max_length=32)
    items: list[BookingItem] = Field(min_length=1, max_length=10)
    delivery: _date
    notes: str = Field(default="", max_length=1000)


# -- read ----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "llm": bool(ws.llm and ws.llm.available)}


@app.get("/api/overview")
def overview():
    c = ws.company
    fc = cashflow.forecast(c)
    return {
        "as_of": c.as_of.isoformat(),
        "profile": c.profile.__dict__,
        "accounts": [a.__dict__ for a in c.accounts],
        "cash_myr": c.cash_myr(),
        "cash_buffer": c.cash_buffer_myr,
        "forecast": {k: fc[k] for k in ("opening_balance", "trough", "funding_gap", "first_buffer_breach",
                                        "first_negative", "weekly")},
        "alerts": fc["alerts"],
        "risk": {k: v for k, v in risk.simulate(c).items() if k in ("p_below_floor", "p_negative", "gap", "sims")},
        "pending_approvals": len(ws.agent.pending()),
        "audit": ws.audit.verify(),
        "llm": bool(ws.llm and ws.llm.available),
        "paused": ws.policy.paused,
        "chain": cashflow.root_cause_chain(c, fc, financing.match_for_forecast(c, fc)),
    }


@app.get("/api/forecast")
def get_forecast():
    return cashflow.forecast(ws.company)


@app.get("/api/analysis")
def get_analysis():
    return analysis.analyse(ws.company)


@app.get("/api/matching")
def invoice_matching():
    c = ws.company
    out = []
    for inv in c.open_invoices():
        if inv.order_id and inv.lines:
            r = matching.three_way_match(c, inv.id)
            o = c.order(inv.order_id)
            dn = c.delivery_for(o.id)
            r.update(issued=inv.issued.isoformat(), due=inv.due.isoformat(), amount=inv.amount,
                     delivered_on=dn.delivered_on.isoformat() if dn else None, signed_by=dn.signed_by if dn else None)
            out.append(r)
    return out


def _order_row(o):
    return {"id": o.id, "customer": o.customer, "po_number": o.po_number, "placed": o.placed.isoformat(),
            "currency": o.currency, "total": o.total, "total_myr": round(o.total * (4.42 if o.currency == "USD" else 1), 2),
            "lines": [{"sku": l.sku, "description": l.description, "qty": l.qty, "unit_price": l.unit_price} for l in o.lines],
            "delivery_date": o.delivery_date.isoformat(), "status": o.status, "channel": o.channel,
            "deposit_pct": o.deposit_pct, "deposit_paid": o.deposit_paid, "deposit_due": o.deposit_due,
            "contact": o.contact, "notes": o.notes, "invoice_id": o.invoice_id, "assessment": o.assessment}


@app.get("/api/orders")
def orders():
    c = ws.company
    return {"orders": [_order_row(o) for o in sorted(c.orders, key=lambda o: o.placed, reverse=True)],
            "catalogue": [p.__dict__ for p in c.catalogue]}


# -- customer channel (the booking portal) ---------------------------------------------
@app.post("/api/bookings")
def create_booking(body: BookingIn):
    """Public: a customer books an order. Prices come from our catalogue, the
    notes are screened like email, and nothing is confirmed here — the agent
    and policy decide, exactly as for any other booking."""
    try:
        o = bookings.new_booking(ws.company, body.customer.strip(), body.email.strip().lower(), body.po_number.strip(),
                                 [(i.sku, i.qty) for i in body.items], body.delivery, body.notes)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ws.audit.record("booking_received", "customer", {"order_id": o.id, "customer": o.customer,
                                                     "verdict": o.assessment["verdict"]})
    ws.audit.seal()
    a = o.assessment
    return {"order_id": o.id, "status": o.status, "total": o.total, "currency": o.currency,
            "message": "Thank you — your booking was received and will be confirmed by email.",
            "deposit_pct": a["deposit_pct"] if a["verdict"] != "decline" else None}


@app.get("/api/financing")
def get_financing():
    fc = cashflow.forecast(ws.company)
    m = financing.match_for_forecast(ws.company, fc) or {"need": 0, "options": [], "recommended_plan": []}
    snap = market.snapshot()
    return {**m, "opr": snap["opr"], "lpr": snap["china"]["lpr"]}


class AttackIn(BaseModel):
    sender: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+$")
    display_name: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)


@app.get("/api/attack/presets")
def attack_presets():
    return arena.PRESETS


@app.post("/api/attack")
def try_attack(body: AttackIn):
    """"Try to fool Tidewise": runs a visitor's email through every defence on
    a throw-away sandbox copy, and records the attempt in the audit trail."""
    r = arena.attack(body.sender, body.display_name, body.subject, body.body)
    ws.audit.record("attack_lab", "visitor", {"sender": guard.mask(body.sender), "verdict": r["verdict"],
                                              "attempt": r["attempt"],
                                              "outcome": (r["decision"] or {}).get("outcome")})
    ws.audit.seal()
    return r


@app.get("/api/market")
def get_market():
    """Live central-bank data: Bank Negara Malaysia (USD/MYR, CNY/MYR, 13-week
    USD volatility, OPR) and China's CFETS (RMB central parity, LPR), each
    value labelled bnm, cfets or fallback."""
    return market.snapshot()


@app.get("/api/inbox")
def inbox():
    msgs = {m.id: m for m in ws.company.inbox}
    return [{**s.to_dict(), "subject": msgs[s.message_id].subject, "display_name": msgs[s.message_id].display_name,
             "received": msgs[s.message_id].received.isoformat()} for s in ws.scans.values()]


@app.get("/api/ledger")
def ledger():
    c = ws.company
    return {
        "receivables": [{"id": i.id, "customer": i.customer, "country": i.country, "currency": i.currency,
                         "amount": i.amount, "due": i.due.isoformat(), "terms": i.terms,
                         "days_overdue": max(0, (c.as_of - i.due).days), "financed": i.financed,
                         "pledged_to": i.pledged_to, "reminders_sent": i.reminders_sent} for i in c.open_invoices()],
        "bills": [{"id": b.id, "payee": c.payee(b.payee_id).name, "amount": b.amount, "currency": b.currency,
                   "due": b.due.isoformat(), "description": b.description,
                   "paid_on": b.paid_on.isoformat() if b.paid_on else None} for b in sorted(c.bills, key=lambda b: b.due)],
        "payees": [{"id": p.id, "name": p.name, "bank": p.bank, "account": "****" + p.account_number[-4:],
                    "verified": p.verified} for p in c.payees],
    }


@app.get("/api/policy")
def policy():
    return {"limits": ws.policy.limits.__dict__, "paused": ws.policy.paused,
            "actions": [{"type": t, "tier": int(v), "label": TIER_LABEL[int(v)]} for t, v in BASE_TIER.items()]}


@app.get("/api/audit")
def audit():
    return {"verification": ws.audit.verify(), "batches": ws.audit.batches(), "records": ws.audit.records[-300:]}


@app.get("/api/audit/receipt/{record_id}")
def audit_receipt(record_id: int):
    r = ws.audit.receipt(record_id)
    if not r:
        raise HTTPException(404, "no sealed record with that id")
    return r


@app.get("/api/sandbox")
def sandbox():
    return {"log": ws.sandbox.log, "outbox": ws.sandbox.outbox, "applications": ws.sandbox.applications,
            "accounts": [a.__dict__ for a in ws.company.accounts]}


# -- conversation channel ------------------------------------------------------------
@app.post("/api/chat")
def chat(body: ChatIn):
    return ws.agent.chat(body.message, body.lang)


@app.get("/api/approvals")
def approvals():
    return sorted(ws.approvals.values(), key=lambda a: a["created_at"], reverse=True)


# -- owner channel ---------------------------------------------------------------------
@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: str, body: ApproveIn, who: str = Depends(require_owner)):
    try:
        return ws.agent.approve(approval_id, who, body.confirm_amount)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: str, who: str = Depends(require_owner)):
    try:
        return ws.agent.reject(approval_id, who)
    except ValueError as e:
        raise HTTPException(409, str(e))


# -- demo controls -----------------------------------------------------------------------
@app.post("/api/simulate/customer-approves")
def customer_approves(body: InvoiceRef):
    """Stands in for the customer's accounts team approving a corrected invoice."""
    from tidewise.sandbox import SandboxError
    try:
        r = ws.sandbox.customer_accepts_correction(body.invoice_id, f"customer-approves-{body.invoice_id}")
    except SandboxError as e:
        raise HTTPException(409, str(e))
    ws.audit.record("external_event", "customer", {"invoice_id": body.invoice_id, "ref": r["ref"]})
    ws.audit.seal()
    return {"receipt": r, "match": matching.three_way_match(ws.company, body.invoice_id)}


@app.post("/api/simulate/deposit")
def customer_pays_deposit(body: OrderRef):
    """Stands in for a customer paying the deposit on a confirmed order."""
    from tidewise.sandbox import SandboxError
    try:
        r = ws.sandbox.record_deposit(body.order_id, f"deposit-{body.order_id}")
    except SandboxError as e:
        raise HTTPException(409, str(e))
    ws.audit.record("external_event", "customer", {"order_id": body.order_id, "ref": r["ref"]})
    ws.audit.seal()
    return {"receipt": r}


@app.get("/api/risk")
def get_risk():
    """Monte Carlo forecast: probability of breaching the floor, gap percentiles,
    fan-chart bands and the receipts that drive the risk."""
    return risk.simulate(ws.company)


@app.get("/api/risk/plan")
def get_risk_plan(target: float = 0.05):
    """Cheapest set of actions that brings the breach probability to `target`.
    Read-only: simulated on a copy of the books. To act on it, the owner asks
    the Copilot ("make me 95% safe"), and every step goes through policy."""
    if not 0.001 <= target <= 0.5:
        raise HTTPException(422, "target must be between 0.001 and 0.5")
    return risk.plan_for_confidence(ws.company, target=target)


@app.post("/api/whatif")
def what_if(body: WhatIfIn):
    scenario = {k: v for k, v in body.model_dump().items() if v not in (None, 0, "")}
    if not scenario:
        raise HTTPException(422, "describe at least one change")
    try:
        return cashflow.whatif(ws.company, scenario)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/autonomy")
def autonomy(body: AutonomyIn, who: str = Depends(require_owner)):
    """Owner kill switch: when paused, nothing executes without approval."""
    ws.policy.paused = body.paused
    ws.audit.record("autonomy_paused" if body.paused else "autonomy_resumed", who, {})
    ws.audit.seal()
    return {"paused": ws.policy.paused}


_ml_report = None


@app.get("/api/ml")
def ml_report():
    """Held-out evaluation of the three risk models (computed once, then cached)."""
    global _ml_report
    if _ml_report is None:
        from tidewise import ml
        _ml_report = ml.evaluate_all()
    return _ml_report


@app.post("/api/redteam")
def run_redteam():
    return redteam.run(write_evidence=True)


@app.post("/api/reset")
def reset(who: str = Depends(require_owner)):
    ws.reset()
    return {"ok": True}
