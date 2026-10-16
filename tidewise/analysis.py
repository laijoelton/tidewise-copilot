"""Business financial analysis from the ledger: margins, working-capital
cycle (DSO / DPO / cash conversion), liquidity and trend, each with a plain
reading of what the number means for the owner."""

from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List

from .models import Company, to_base

WINDOW_DAYS = 90


def _myr(company: Company, tx) -> float:
    return to_base(tx.amount, company.account(tx.account_id).currency)


def analyse(company: Company) -> Dict[str, Any]:
    start = company.as_of - timedelta(days=WINDOW_DAYS)
    window = [t for t in company.transactions if start <= t.on < company.as_of and t.category != "fx"]

    by_cat = defaultdict(float)
    for t in window:
        by_cat[t.category] += _myr(company, t)
    revenue = round(by_cat.pop("revenue", 0.0), 2)
    expenses = {k: round(-v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: kv[1])}
    cogs = expenses.get("cogs", 0.0)
    opex = round(sum(v for k, v in expenses.items() if k not in ("cogs", "loan")), 2)

    ar = round(sum(to_base(i.amount, i.currency) for i in company.open_invoices()), 2)
    ap = round(sum(to_base(b.amount, b.currency) for b in company.open_bills()), 2)
    cash = company.cash_myr()
    loan_12m = sum(r.amount for r in company.recurring if r.category == "loan") * 12

    daily_rev = revenue / WINDOW_DAYS if revenue else 0.0
    daily_cogs = cogs / WINDOW_DAYS if cogs else 0.0
    dso = round(ar / daily_rev, 1) if daily_rev else None
    dpo = round(ap / daily_cogs, 1) if daily_cogs else None
    gross_margin = round((revenue - cogs) / revenue, 3) if revenue else None
    op_margin = round((revenue - cogs - opex) / revenue, 3) if revenue else None
    current_ratio = round((cash + ar) / (ap + loan_12m), 2) if (ap + loan_12m) else None
    monthly_out = round(sum(expenses.values()) / (WINDOW_DAYS / 30), 2)

    monthly = defaultdict(lambda: {"revenue": 0.0, "costs": 0.0})
    for t in company.transactions:
        if t.category == "fx" or t.on >= company.as_of:
            continue
        key = t.on.strftime("%Y-%m")
        v = _myr(company, t)
        if t.category == "revenue":
            monthly[key]["revenue"] += v
        else:
            monthly[key]["costs"] += -v
    trend = [{"month": k, "revenue": round(v["revenue"], 2), "costs": round(v["costs"], 2)}
             for k, v in sorted(monthly.items())][-6:]

    metrics = {
        "revenue_90d": revenue,
        "cogs_90d": cogs,
        "opex_90d": opex,
        "gross_margin": gross_margin,
        "operating_margin": op_margin,
        "dso_days": dso,
        "dpo_days": dpo,
        "cash_conversion_gap_days": round(dso - dpo, 1) if dso is not None and dpo is not None else None,
        "current_ratio": current_ratio,
        "cash": cash,
        "receivables": ar,
        "payables": ap,
        "monthly_outflow": monthly_out,
        "cash_runway_months": round(cash / monthly_out, 1) if monthly_out else None,
    }
    return {"window_days": WINDOW_DAYS, "metrics": metrics, "expenses_90d": expenses,
            "trend": trend, "insights": insights(metrics)}


def insights(m: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    gap = m["cash_conversion_gap_days"]
    if gap is not None and gap > 7:
        out.append({"tone": "warning", "title": f"You fund customers for {gap:g} days",
                    "detail": f"Customers take {m['dso_days']:g} days to pay you, you take {m['dpo_days']:g} days to pay "
                              "suppliers. Every extra sale widens the gap — growth needs working-capital finance."})
    if m["cash_runway_months"] is not None and m["cash_runway_months"] < 2:
        out.append({"tone": "warning", "title": f"{m['cash_runway_months']:g} months of cash on hand",
                    "detail": "Cash covers less than two months of outgoings without new receipts."})
    if m["gross_margin"] is not None and m["operating_margin"] is not None:
        tone = "good" if m["operating_margin"] > 0 else "warning"
        out.append({"tone": tone, "title": f"Gross margin {m['gross_margin']:.0%}, operating margin {m['operating_margin']:.0%}",
                    "detail": "Materials take {:.0%} of revenue; wages, rent and logistics take another {:.0%}.".format(
                        1 - m["gross_margin"], m["gross_margin"] - m["operating_margin"])})
    if m["current_ratio"] is not None:
        tone = "good" if m["current_ratio"] >= 1.2 else "warning"
        out.append({"tone": tone, "title": f"Current ratio {m['current_ratio']:g}",
                    "detail": "Short-term assets comfortably cover short-term obligations." if tone == "good"
                              else "Short-term assets barely cover the next year's obligations; lenders look for 1.2+."})
    return out
