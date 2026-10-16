"""13-week cash-flow forecast with alerts.

Receipts are dated by what each customer actually does, not what the invoice
says: the expected date is the due date plus that customer's average payment
delay over the last six months. Letter-of-credit receivables are pushed out by
the re-presentation cycle when the trade-document check finds a discrepancy.
Receivables far past their customer's norm are treated as doubtful and left
out of the forecast instead of being counted as cash.
"""

from collections import defaultdict
from datetime import date, timedelta
from statistics import mean
from typing import Any, Dict, List

from .models import Company, Invoice, to_base
from .trade_docs import check_shipment

DOUBTFUL_AFTER_DAYS = 30        # this many days past the customer's own norm
CONCENTRATION_LIMIT = 0.35
# Beyond the books' visibility (no invoices raised yet, no bills received yet)
# the forecast falls back to trailing run-rates, starting this many days out
# and with a haircut on sales — projections are labelled as such.
RUN_RATE_FROM_DAY = 35
RUN_RATE_SALES_HAIRCUT = 0.85
RUN_RATE_WINDOW_DAYS = 90


def customer_payment_delays(company: Company) -> Dict[str, float]:
    delays = defaultdict(list)
    for inv in company.invoices:
        if inv.paid_on:
            delays[inv.customer].append((inv.paid_on - inv.due).days)
    return {c: round(mean(v), 1) for c, v in delays.items()}


def _receipt_plan(company: Company, inv: Invoice, delays: Dict[str, float]) -> Dict[str, Any]:
    norm = delays.get(inv.customer, 0.0)
    expected = inv.due + timedelta(days=round(norm))
    reason = f"due {inv.due.isoformat()} + {norm:g}d average delay for {inv.customer}"
    doc_check = None
    if inv.terms == "LC" and inv.shipment_id:
        doc_check = check_shipment(company, inv.shipment_id)
        if doc_check["blocks_lc_payment"]:
            expected = expected + timedelta(days=doc_check["expected_delay_days"])
            fields = ", ".join(d["label"] for d in doc_check["discrepancies"])
            reason += f"; +{doc_check['expected_delay_days']}d LC refusal risk ({fields} discrepancy)"
    days_past_norm = (company.as_of - (inv.due + timedelta(days=round(norm)))).days
    doubtful = days_past_norm > DOUBTFUL_AFTER_DAYS
    if expected < company.as_of:
        expected = company.as_of + timedelta(days=3)   # overdue but collectible: assume soon
        reason += "; overdue, assumed within 3 days"
    return {"expected": expected, "reason": reason, "doubtful": doubtful,
            "days_overdue": max(0, (company.as_of - inv.due).days), "doc_check": doc_check}


def _recurring_dates(day_of_month: int, start: date, end: date) -> List[date]:
    out, d = [], start
    while d <= end:
        if d.day == day_of_month:
            out.append(d)
        d += timedelta(days=1)
    return out


def _run_rate_events(company: Company, end: date) -> List[Dict[str, Any]]:
    start = company.as_of - timedelta(days=RUN_RATE_WINDOW_DAYS)
    weeks = RUN_RATE_WINDOW_DAYS / 7
    sums = defaultdict(float)
    for t in company.transactions:
        if start <= t.on < company.as_of and t.category in ("revenue", "cogs", "logistics"):
            sums[t.category] += to_base(t.amount, company.account(t.account_id).currency)
    sales = sums["revenue"] / weeks * RUN_RATE_SALES_HAIRCUT
    costs = -(sums["cogs"] + sums["logistics"]) / weeks
    out, day = [], company.as_of + timedelta(days=RUN_RATE_FROM_DAY)
    while day <= end:
        out.append({"date": day, "direction": "in", "amount_myr": round(sales, 2), "label": "Projected new sales (run-rate)",
                    "source": "projection", "basis": f"trailing 90-day sales x {RUN_RATE_SALES_HAIRCUT}", "projected": True})
        out.append({"date": day, "direction": "out", "amount_myr": round(costs, 2), "label": "Projected materials & logistics (run-rate)",
                    "source": "projection", "basis": "trailing 90-day materials + logistics", "projected": True})
        day += timedelta(days=7)
    return out


def build_events(company: Company, horizon_days: int) -> Dict[str, Any]:
    end = company.as_of + timedelta(days=horizon_days - 1)
    delays = customer_payment_delays(company)
    events, excluded = [], []

    for inv in company.open_invoices():
        if inv.financed:
            continue
        plan = _receipt_plan(company, inv, delays)
        entry = {
            "date": plan["expected"], "direction": "in", "amount_myr": to_base(inv.amount, inv.currency),
            "label": f"{inv.customer} — {inv.id}", "source": inv.id, "basis": plan["reason"],
        }
        if plan["doubtful"]:
            excluded.append({**entry, "why": f"{plan['days_overdue']} days overdue, well past this customer's norm"})
        elif plan["expected"] <= end:
            events.append(entry)

    for bill in company.open_bills():
        if bill.due <= end:
            payee = company.payee(bill.payee_id)
            events.append({
                "date": max(bill.due, company.as_of), "direction": "out", "amount_myr": to_base(bill.amount, bill.currency),
                "label": f"{payee.name if payee else bill.payee_id} — {bill.id}", "source": bill.id,
                "basis": f"bill due {bill.due.isoformat()}",
            })

    for rp in company.repayments:
        if company.as_of <= rp["date"] <= end:
            events.append({"date": rp["date"], "direction": "out", "amount_myr": rp["amount_myr"],
                           "label": rp["label"], "source": rp["source"], "basis": "lender repayment"})

    events += _run_rate_events(company, end)

    for r in company.recurring:
        for on in _recurring_dates(r.day_of_month, company.as_of, end):
            events.append({"date": on, "direction": "out", "amount_myr": r.amount, "label": r.name,
                           "source": f"recurring:{r.category}", "basis": f"every month on day {r.day_of_month}"})

    events.sort(key=lambda e: (e["date"], e["direction"] == "out"))
    return {"events": events, "excluded": excluded, "delays": delays}


def forecast(company: Company, weeks: int = 13) -> Dict[str, Any]:
    horizon = weeks * 7
    built = build_events(company, horizon)
    events = built["events"]
    opening = company.cash_myr()
    buffer = company.cash_buffer_myr

    # Daily running balance, so breach dates are exact rather than per-week.
    balance, daily, by_day = opening, [], defaultdict(list)
    for e in events:
        by_day[e["date"]].append(e)
    trough = {"date": company.as_of.isoformat(), "balance": opening}
    first_breach = first_negative = None
    for i in range(horizon):
        day = company.as_of + timedelta(days=i)
        for e in by_day.get(day, []):
            balance += e["amount_myr"] if e["direction"] == "in" else -e["amount_myr"]
        balance = round(balance, 2)
        daily.append({"date": day.isoformat(), "balance": balance})
        if balance < trough["balance"]:
            trough = {"date": day.isoformat(), "balance": balance}
        if balance < buffer and first_breach is None:
            first_breach = day.isoformat()
        if balance < 0 and first_negative is None:
            first_negative = day.isoformat()

    weekly = []
    for w in range(weeks):
        start = company.as_of + timedelta(days=7 * w)
        stop = start + timedelta(days=6)
        wk = [e for e in events if start <= e["date"] <= stop]
        weekly.append({
            "week": w + 1,
            "start": start.isoformat(),
            "end": stop.isoformat(),
            "inflow": round(sum(e["amount_myr"] for e in wk if e["direction"] == "in"), 2),
            "outflow": round(sum(e["amount_myr"] for e in wk if e["direction"] == "out"), 2),
            "closing": daily[7 * w + 6]["balance"],
        })

    gap = round(max(0.0, buffer - trough["balance"]), 2)
    result = {
        "as_of": company.as_of.isoformat(),
        "currency": "MYR",
        "opening_balance": opening,
        "cash_buffer": buffer,
        "weekly": weekly,
        "daily": daily,
        "trough": trough,
        "first_buffer_breach": first_breach,
        "first_negative": first_negative,
        "funding_gap": gap,
        "events": [{**e, "date": e["date"].isoformat()} for e in events],
        "excluded": [{**e, "date": e["date"].isoformat()} for e in built["excluded"]],
        "customer_delays": built["delays"],
    }
    result["alerts"] = alerts(company, result)
    return result


def alerts(company: Company, fc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    if fc["first_negative"]:
        out.append({"severity": "critical", "code": "CASH_SHORTFALL",
                    "title": f"Account goes negative on {fc['first_negative']}",
                    "detail": f"Lowest projected balance RM {fc['trough']['balance']:,.0f} on {fc['trough']['date']}. "
                              f"RM {fc['funding_gap']:,.0f} needed to stay above your RM {fc['cash_buffer']:,.0f} floor."})
    elif fc["first_buffer_breach"]:
        out.append({"severity": "warning", "code": "BUFFER_BREACH",
                    "title": f"Cash drops below your RM {fc['cash_buffer']:,.0f} floor on {fc['first_buffer_breach']}",
                    "detail": f"Lowest projected balance RM {fc['trough']['balance']:,.0f} on {fc['trough']['date']}."})

    for s in company.shipments:
        chk = check_shipment(company, s.id)
        if chk["blocks_lc_payment"]:
            inv = company.invoice(chk["invoice_id"])
            d = chk["discrepancies"][0]
            out.append({"severity": "critical", "code": "LC_DISCREPANCY",
                        "title": f"{d['label']} mismatch on {s.id} puts {inv.currency} {inv.amount:,.0f} at risk",
                        "detail": f"Draft B/L says {d['bl_value']}, shipping instruction says {d['si_value']}. "
                                  f"The bank will refuse the LC presentation; payment slips ~{chk['expected_delay_days']} days "
                                  f"unless the B/L is amended before {(s.lc_terms or {}).get('presentation_deadline', 'presentation')}.",
                        "ref": s.id})

    delays = fc["customer_delays"]
    for inv in company.open_invoices():
        overdue = (company.as_of - inv.due).days
        if overdue > 0:
            doubtful = any(x["source"] == inv.id for x in fc["excluded"])
            out.append({"severity": "warning" if doubtful else "info",
                        "code": "DOUBTFUL_RECEIVABLE" if doubtful else "OVERDUE_RECEIVABLE",
                        "title": f"{inv.id} from {inv.customer} is {overdue} days overdue",
                        "detail": f"{inv.currency} {inv.amount:,.0f}; this customer normally pays "
                                  f"{delays.get(inv.customer, 0):g} days after due. "
                                  + ("Excluded from the forecast as doubtful." if doubtful else "Send a reminder."),
                        "ref": inv.id})

    open_ar = [(i, to_base(i.amount, i.currency)) for i in company.open_invoices()]
    total = sum(v for _, v in open_ar)
    if total:
        by_customer = defaultdict(float)
        for i, v in open_ar:
            by_customer[i.customer] += v
        top, top_v = max(by_customer.items(), key=lambda kv: kv[1])
        if top_v / total > CONCENTRATION_LIMIT:
            out.append({"severity": "info", "code": "CONCENTRATION",
                        "title": f"{top} is {top_v / total:.0%} of what you're owed",
                        "detail": "One late payment from this customer moves your whole cash position."})
        foreign = sum(v for i, v in open_ar if i.currency != "MYR")
        if foreign / total > 0.5:
            out.append({"severity": "info", "code": "FX_EXPOSURE",
                        "title": f"{foreign / total:.0%} of receivables are in foreign currency",
                        "detail": f"A 3% move in USD/MYR changes your receivables by about RM {foreign * 0.03:,.0f}."})

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda a: order[a["severity"]])
