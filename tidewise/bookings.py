"""Customer bookings: orders placed through the customer portal (or by email).

Every new booking is assessed before anyone commits factory capacity:

  1. Screening — the booking's free-text notes are external content, so they
     go through the same guard as email (prompt injection, payment-redirect
     fraud, ML threat score). A quarantined booking is declined.
  2. Identity — is this a known customer, and does the booking come from the
     contact we have on file for them? A known name from an unknown address is
     treated like a new customer.
  3. Credit — the ML collection-risk model scores the customer from their own
     payment history; a new customer has none, so starts at a cautious prior.
  4. Deposit — risk decides the deposit Tidewise asks for (30 / 40 / 50%).
  5. Capacity — the factory's weekly output per product against everything
     already in the order book gives the earliest ready date.

The assessment is advice. Whether a booking is confirmed automatically or
waits for the owner is decided by policy.Policy (action "confirm_booking").
"""

import math
from datetime import date, timedelta
from typing import Any, Dict

from . import guard
from .models import Company, InboundMessage, Invoice, Order, to_base

NEW_CUSTOMER_RISK = 0.5          # prior late-payment risk without any history
AUTO_CONFIRM_MAX_MYR = 50_000
AUTO_CONFIRM_MAX_RISK = 0.3


def recommended_deposit(risk: float, new_customer: bool) -> float:
    if new_customer or risk >= 0.5:
        return 0.5
    return 0.3 if risk < 0.2 else 0.4


def _production_weeks(company: Company, order: Order) -> float:
    """Weeks of factory time this order needs behind everything already booked."""
    load: Dict[str, int] = {}
    for o in company.orders:
        if o.status in ("confirmed", "in_production") and o.id != order.id:
            for l in o.lines:
                load[l.sku] = load.get(l.sku, 0) + l.qty
    weeks = 0.0
    for l in order.lines:
        p = company.product(l.sku)
        if p is None:
            return math.inf
        weeks = max(weeks, (load.get(l.sku, 0) + l.qty) / p.units_per_week)
    return round(weeks, 1)


def assess(company: Company, order: Order) -> Dict[str, Any]:
    # Display name left empty: a known customer's name from a new address is
    # an identity question for the owner, not impersonation by itself.
    # Look-alike and homoglyph domains are still caught by the sender checks.
    msg = InboundMessage(f"BOOKING-{order.id}", order.placed, order.contact or "unknown@unknown.example",
                         "", f"Booking {order.po_number}", order.notes or "")
    scan = guard.scan(company, msg)
    findings = [f.detail for f in scan.findings if f.severity in ("critical", "warning")
                and f.check not in ("sender_unknown",)]

    known = company.known_customer(order.customer)
    registered = company.customer_contacts.get(order.customer)
    contact_ok = known and registered == order.contact
    if known and contact_ok:
        from . import ml
        probe = Invoice("PROBE", order.customer, company.customer_countries.get(order.customer, "?"),
                        order.currency, order.total, company.as_of, company.as_of + timedelta(days=30))
        risk = ml.collection_model().score(company, probe)
        risk_basis = "ML collection-risk model on this customer's payment history"
    else:
        risk = NEW_CUSTOMER_RISK
        risk_basis = "no payment history with us — cautious starting point"

    deposit = recommended_deposit(risk, not contact_ok)
    weeks = _production_weeks(company, order)
    ready = company.as_of + timedelta(days=math.ceil(weeks * 7) + 7) if weeks != math.inf else None
    feasible = ready is not None and ready <= order.delivery_date
    value = to_base(order.total, order.currency)

    if scan.trust == "quarantined":
        verdict, why = "decline", "notes contain instructions aimed at the AI or a payment redirect"
    elif contact_ok and risk <= AUTO_CONFIRM_MAX_RISK and value <= AUTO_CONFIRM_MAX_MYR and feasible:
        verdict, why = "confirm", "known customer, low risk, within limits and capacity"
    else:
        reasons = []
        if not known:
            reasons.append("new customer")
        elif not contact_ok:
            reasons.append(f"booked from {order.contact}, not the contact on file")
        if risk > AUTO_CONFIRM_MAX_RISK:
            reasons.append(f"late-payment risk {risk:.0%}")
        if value > AUTO_CONFIRM_MAX_MYR:
            reasons.append(f"large order (RM {value:,.0f})")
        if not feasible:
            reasons.append("can't be ready by the requested date")
        verdict, why = "owner", "; ".join(reasons)

    return {
        "screening": {"trust": scan.trust, "findings": findings, "ml": scan.ml},
        "scan": scan.to_dict(),
        "known_customer": known, "contact_matches": bool(contact_ok),
        "risk": round(risk, 3), "risk_basis": risk_basis,
        "deposit_pct": deposit, "deposit_amount": round(order.total * deposit, 2),
        "value_myr": value, "production_weeks": weeks,
        "ready_by": ready.isoformat() if ready else None, "feasible": feasible,
        "verdict": verdict, "why": why,
    }


def assess_pending(company: Company) -> None:
    for o in company.orders:
        if o.status == "pending":
            o.assessment.update(assess(company, o))


def new_booking(company: Company, customer: str, contact: str, po_number: str, items, delivery: date,
                notes: str = "") -> Order:
    """A booking arriving from the customer portal. Prices always come from our
    own catalogue — never from the customer's form."""
    from .models import Line
    lines = []
    for sku, qty in items:
        p = company.product(sku)
        if p is None or qty <= 0:
            raise ValueError(f"unknown product or quantity: {sku} x {qty}")
        lines.append(Line(p.sku, p.name, int(qty), p.unit_price_usd))
    oid = f"ORD-{330 + sum(1 for o in company.orders if o.channel == 'portal' and o.id >= 'ORD-330')}"
    order = Order(oid, customer, po_number, company.as_of, "USD", lines, delivery, "pending",
                  channel="portal", contact=contact, notes=notes)
    company.orders.append(order)
    order.assessment.update(assess(company, order))
    return order
