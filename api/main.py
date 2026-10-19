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
from tidewise import analysis, cashflow, financing, redteam, trade_docs  # noqa: E402
from tidewise.agent import Workspace  # noqa: E402
from tidewise.policy import BASE_TIER, TIER_LABEL  # noqa: E402

OWNER_TOKEN = os.environ.get("TIDEWISE_OWNER_TOKEN") or "demo-owner-" + secrets.token_hex(4)
ALLOWED_ORIGINS = os.environ.get("TIDEWISE_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

app = FastAPI(title="Tidewise API", version="0.1")
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


# -- models --------------------------------------------------------------------------
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


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


class SimulateIn(BaseModel):
    shipment_id: str = Field(pattern=r"^SHP-\d{3}$")


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


@app.get("/api/documents")
def documents():
    out = []
    for s in ws.company.shipments:
        r = trade_docs.check_shipment(ws.company, s.id)
        r["si_text"], r["bl_text"] = s.si_text, s.bl_text
        out.append(r)
    return out


@app.get("/api/financing")
def get_financing():
    fc = cashflow.forecast(ws.company)
    return financing.match_for_forecast(ws.company, fc) or {"need": 0, "options": [], "recommended_plan": []}


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
    return ws.agent.chat(body.message)


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
@app.post("/api/simulate/forwarder-reply")
def forwarder_reply(body: SimulateIn):
    """Stands in for the forwarder emailing back a corrected draft B/L."""
    if ws.company.shipment(body.shipment_id) is None:
        raise HTTPException(404, "unknown shipment")
    r = ws.sandbox.apply_bl_amendment(body.shipment_id, f"forwarder-reply-{body.shipment_id}")
    ws.audit.record("external_event", "forwarder", {"shipment_id": body.shipment_id, "ref": r["ref"]})
    ws.audit.seal()
    return {"receipt": r, "check": trade_docs.check_shipment(ws.company, body.shipment_id)}


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


@app.post("/api/redteam")
def run_redteam():
    return redteam.run(write_evidence=True)


@app.post("/api/reset")
def reset(who: str = Depends(require_owner)):
    ws.reset()
    return {"ok": True}
