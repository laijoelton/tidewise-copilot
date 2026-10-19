"""Financing-option matching.

Given a funding need (amount + the date cash breaches the floor), every
product in the catalogue is checked rule by rule against the business's own
data. The result says, for each product: is it eligible and why or why not,
how much it can provide, whether money arrives before it is needed, and what
it costs in ringgit and as an effective annual rate. Nothing is ranked on
"AI judgement": the order is eligible -> on time -> covers the need -> cost.

The catalogue is illustrative (fictional providers, indicative market-shaped
pricing). It is data, not code — swapping in a real lender panel is a file
change.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .models import Company, Invoice, to_base
from .trade_docs import check_shipment

ELIGIBLE_BUYER_COUNTRIES = {"AU", "JP", "GB", "NL", "US", "SG", "DE", "FR", "MY", "KR", "NZ", "CA"}
HIGH_COST_APR = 0.36
FEE_MARGIN = 0.03          # plans raise the gap + 3% so fees don't reopen it, rounded up to RM 1,000
BANDS = "ABCDE"


@dataclass
class Product:
    id: str
    name: str
    provider: str
    kind: str                       # invoice | lc | revolving | term | supplier | mca
    days_to_fund: tuple
    summary: str
    min_years: float = 0
    min_revenue: float = 0
    worst_band: str = "E"
    needs_collateral: bool = False
    advance_rate: float = 1.0
    monthly_rate: float = 0.0       # charged per 30 days outstanding
    annual_rate: float = 0.0
    upfront_fee: float = 0.0        # fraction of amount drawn
    factor: float = 0.0             # merchant cash advance: repay amount x factor
    limit_pct_revenue: float = 0.0
    max_limit: float = 0.0
    tags: List[str] = field(default_factory=list)


CATALOGUE: List[Product] = [
    Product("INVFIN", "Export invoice financing", "Lumen Trade Capital (fictional)", "invoice", (1, 3),
            "Advance 80% of unpaid open-account invoices; repaid when the buyer pays.",
            min_years=1, worst_band="D", advance_rate=0.80, monthly_rate=0.012, upfront_fee=0.005),
    Product("LCDISC", "LC bill discounting", "Your trade bank (fictional)", "lc", (1, 2),
            "Bank buys the complying LC presentation at a discount; cheapest money an exporter can get.",
            worst_band="E", advance_rate=0.95, annual_rate=0.068, upfront_fee=0.0025),
    Product("SUPTERMS", "Supplier terms extension", "Your long-standing supplier", "supplier", (2, 5),
            "Ask a long-standing supplier to move a bill 30 days later for a small surcharge.",
            upfront_fee=0.015, tags=["negotiated"]),
    Product("WCLINE", "Working-capital revolving line", "Seri Commercial Bank (fictional)", "revolving", (14, 21),
            "Unsecured revolving line sized to revenue; slow to set up, cheap and reusable once in place.",
            min_years=2, min_revenue=1_000_000, worst_band="C", annual_rate=0.089, upfront_fee=0.01,
            limit_pct_revenue=0.10, max_limit=500_000),
    Product("GGSME", "Government-guaranteed SME term loan", "SME guarantee scheme (illustrative)", "term", (21, 35),
            "Term loan with a public guarantee; low rate, slow, good for planned investment rather than a gap.",
            min_years=1, min_revenue=300_000, worst_band="D", annual_rate=0.065, upfront_fee=0.015,
            limit_pct_revenue=0.20, max_limit=1_000_000),
    Product("MCA", "Merchant cash advance", "QuickFund Online (fictional)", "mca", (1, 2),
            "Fast lump sum repaid from future takings; very expensive.",
            worst_band="E", factor=1.18, limit_pct_revenue=1 / 12, max_limit=200_000, tags=["high-cost"]),
]


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


def _eligible_invoices(company: Company) -> List[Invoice]:
    horizon = company.as_of + timedelta(days=90)
    return [i for i in company.open_invoices()
            if i.terms == "OPEN_ACCOUNT" and not i.financed and not i.pledged_to and i.due >= company.as_of
            and i.due <= horizon and i.country in ELIGIBLE_BUYER_COUNTRIES
            and to_base(i.amount, i.currency) >= 20_000]


def _capacity(company: Company, p: Product, checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    if p.kind == "invoice":
        invs = _eligible_invoices(company)
        face = sum(to_base(i.amount, i.currency) for i in invs)
        checks.append(_check("eligible invoices", bool(invs),
                             f"{len(invs)} open-account invoices, not overdue, due within 90 days, ≥ RM 20k: "
                             + (", ".join(i.id for i in invs) or "none")))
        return {"amount": round(face * p.advance_rate, 2), "basis": [i.id for i in invs],
                "repay_on": max((i.due for i in invs), default=None)}
    if p.kind == "lc":
        lc_invs = [i for i in company.open_invoices() if i.terms == "LC" and i.shipment_id and not i.financed]
        clean, blocked = [], []
        for i in lc_invs:
            chk = check_shipment(company, i.shipment_id)
            (blocked if chk["blocks_lc_payment"] else clean).append((i, chk))
        for i, chk in blocked:
            fields = ", ".join(d["label"] for d in chk["discrepancies"])
            checks.append(_check(f"complying presentation ({i.id})", False,
                                 f"{i.shipment_id} has a {fields} discrepancy — the bank will not discount a "
                                 "non-complying presentation. Amend the B/L first."))
        if clean:
            checks.append(_check("complying presentation", True, ", ".join(i.id for i, _ in clean)))
        face = sum(to_base(i.amount, i.currency) for i, _ in clean)
        return {"amount": round(face * p.advance_rate, 2), "basis": [i.id for i, _ in clean],
                "repay_on": max((i.due for i, _ in clean), default=None)}
    if p.kind == "supplier":
        cand = supplier_extension_candidate(company)
        if cand is None:
            checks.append(_check("relationship history", False,
                                 "no large bill due soon from a supplier you have paid regularly (12+ times in 6 months)"))
            return {"amount": 0.0, "basis": [], "repay_on": None}
        bill, payee, n = cand
        checks.append(_check("relationship history", True,
                             f"{payee.name}: {n} payments in the last 6 months; {bill.id} RM {bill.amount:,.0f} due {bill.due.isoformat()}"))
        return {"amount": round(bill.amount, 2), "basis": [bill.id], "repay_on": None}
    limit = min(company.profile.annual_revenue_myr * p.limit_pct_revenue, p.max_limit)
    return {"amount": round(limit, 2), "basis": ["revenue-sized limit"], "repay_on": None}


SUPPLIER_MIN_PAYMENTS = 12
SUPPLIER_WINDOW_DAYS = 21


def supplier_extension_candidate(company: Company):
    """The largest open bill due within the next three weeks from a supplier
    the business has paid regularly — the one a supplier is most likely to
    agree to move. Returns (bill, payee, payment_count) or None."""
    best = None
    for bill in company.open_bills():
        if bill.due > company.as_of + timedelta(days=SUPPLIER_WINDOW_DAYS):
            continue
        payee = company.payee(bill.payee_id)
        if payee is None:
            continue
        n = sum(1 for t in company.transactions if t.counterparty == payee.name and t.amount < 0)
        if n >= SUPPLIER_MIN_PAYMENTS and (best is None or bill.amount > best[0].amount):
            best = (bill, payee, n)
    return best


def _cost(p: Product, amount: float, days: int) -> float:
    if amount <= 0:
        return 0.0
    if p.kind == "mca":
        return round(amount * (p.factor - 1), 2)
    interest = amount * (p.monthly_rate * days / 30 + p.annual_rate * days / 365)
    return round(interest + amount * p.upfront_fee, 2)


def evaluate(company: Company, p: Product, need: float, need_by: date, tenor_days: int) -> Dict[str, Any]:
    prof = company.profile
    checks = []
    if p.min_years:
        checks.append(_check("years trading", prof.years_trading >= p.min_years,
                             f"{prof.years_trading:g} years (needs {p.min_years:g}+)"))
    if p.min_revenue:
        checks.append(_check("annual revenue", prof.annual_revenue_myr >= p.min_revenue,
                             f"RM {prof.annual_revenue_myr:,.0f} (needs RM {p.min_revenue:,.0f}+)"))
    if p.worst_band != "E":
        checks.append(_check("credit band", BANDS.index(prof.credit_band) <= BANDS.index(p.worst_band),
                             f"band {prof.credit_band} (accepts {p.worst_band} or better)"))
    if p.needs_collateral:
        checks.append(_check("collateral", prof.has_collateral, "requires property or fixed-deposit collateral"))

    cap = _capacity(company, p, checks)
    eligible = all(c["passed"] for c in checks) and cap["amount"] > 0
    draw = min(need, cap["amount"]) if eligible else 0.0
    funds_by = company.as_of + timedelta(days=p.days_to_fund[1])
    days = tenor_days
    if cap.get("repay_on"):
        days = max(1, (cap["repay_on"] - funds_by).days)
    elif p.kind == "supplier":
        days = 30
    cost = _cost(p, draw, days)
    apr = round(cost / draw * 365 / days, 4) if draw and days else None

    return {
        "id": p.id, "name": p.name, "provider": p.provider, "kind": p.kind, "summary": p.summary,
        "eligible": eligible,
        "checks": checks,
        "available": cap["amount"] if eligible else 0.0,
        "basis": cap["basis"],
        "draw": round(draw, 2),
        "covers_need": eligible and cap["amount"] >= need,
        "funds_by": funds_by.isoformat(),
        "on_time": funds_by <= need_by,
        "tenor_days": days,
        "cost": cost,
        "effective_apr": apr,
        "high_cost": apr is not None and apr > HIGH_COST_APR,
        "days_to_fund": list(p.days_to_fund),
    }


def match(company: Company, need: float, need_by: date, tenor_days: int = 30) -> Dict[str, Any]:
    options = [evaluate(company, p, need, need_by, tenor_days) for p in CATALOGUE]

    def rank(o):
        return (not o["eligible"], not o["on_time"], o["high_cost"], not o["covers_need"],
                o["effective_apr"] if o["effective_apr"] is not None else 9e9)
    options.sort(key=rank)

    # Cheapest on-time, not-high-cost combination that closes the gap.
    usable = sorted([o for o in options if o["eligible"] and o["on_time"] and not o["high_cost"]],
                    key=lambda o: o["effective_apr"] or 9e9)
    target = math.ceil(need * (1 + FEE_MARGIN) / 1000) * 1000
    plan, remaining = [], target
    for o in usable:
        if remaining <= 0:
            break
        take = round(min(remaining, o["available"]), 2)
        p = next(x for x in CATALOGUE if x.id == o["id"])
        plan.append({"id": o["id"], "name": o["name"], "amount": take, "cost": _cost(p, take, o["tenor_days"]),
                     "funds_by": o["funds_by"]})
        remaining = round(remaining - take, 2)

    return {
        "need": round(need, 2),
        "target_with_fee_margin": target,
        "need_by": need_by.isoformat(),
        "options": options,
        "recommended_plan": plan,
        "plan_covers_need": remaining <= 0,
        "plan_total_cost": round(sum(x["cost"] for x in plan), 2),
        "unmet": max(0.0, remaining),
    }


def match_for_forecast(company: Company, fc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if fc["funding_gap"] <= 0:
        return None
    need_by = date.fromisoformat(fc["first_buffer_breach"])
    recover = next((d["date"] for d in fc["daily"]
                    if d["date"] > fc["trough"]["date"] and d["balance"] >= fc["cash_buffer"]), None)
    tenor = max(14, (date.fromisoformat(recover) - need_by).days) if recover else 60
    return match(company, fc["funding_gap"], need_by, tenor)
