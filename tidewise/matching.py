"""Three-way match: purchase order ↔ delivery note ↔ invoice.

Most business customers pay an invoice only after their accounts team has
matched it to their purchase order (what they agreed to buy, at what price)
and the delivery note (what actually arrived). If quantities or prices
disagree, the invoice is put on hold until a corrected one arrives — and it
then waits for the next payment run.

Tidewise runs the same check on its own invoices *before* the customer does,
so a typo is caught while there is still time to fix it, and the forecast
knows what an unfixed mismatch will cost in days.
"""

from typing import Any, Dict, List, Optional

from .models import Company, Invoice

# Days a mismatched invoice is held: dispute, correction, re-approval and the
# customer's next payment run. Assumptions, shown on every forecast line.
HOLD_DAYS_UNRESOLVED = 21
HOLD_DAYS_CORRECTION_SENT = 14       # re-approval + the customer's next payment run
PRICE_TOLERANCE = 0.005          # 0.5% — typical AP tolerance for rounding


def three_way_match(company: Company, invoice_id: str) -> Dict[str, Any]:
    inv = company.invoice(invoice_id)
    if inv is None:
        raise KeyError(invoice_id)
    order = company.order(inv.order_id) if inv.order_id else None
    delivery = company.delivery_for(order.id) if order else None
    result: Dict[str, Any] = {
        "invoice_id": inv.id, "customer": inv.customer, "currency": inv.currency,
        "order_id": order.id if order else None, "po_number": order.po_number if order else None,
        "delivery_id": delivery.id if delivery else None,
        "correction": inv.correction, "rows": [], "discrepancies": [],
    }
    if order is None or not inv.lines:
        result.update(status="NO_ORDER", blocks_payment=False, expected_delay_days=0,
                      note="No purchase order on file — nothing to match against.")
        return result

    po = {l.sku: l for l in order.lines}
    dn = {l.sku: l for l in (delivery.lines if delivery else [])}
    iv = {l.sku: l for l in inv.lines}
    issues: List[Dict[str, Any]] = []

    for sku in list(dict.fromkeys(list(po) + list(dn) + list(iv))):
        p, d, i = po.get(sku), dn.get(sku), iv.get(sku)
        row = {"sku": sku, "description": (p or i or d).description,
               "ordered": p.qty if p else None, "delivered": d.qty if d else None,
               "invoiced": i.qty if i else None,
               "po_price": p.unit_price if p else None, "invoice_price": i.unit_price if i else None,
               "flags": []}
        if i and not p:
            row["flags"].append("not_ordered")
            issues.append({"field": "line", "sku": sku, "label": f"{row['description']} is not on the PO",
                           "detail": f"invoiced {i.qty} but the customer never ordered it"})
        if p and i and i.qty != p.qty:
            row["flags"].append("qty")
            issues.append({"field": "quantity", "sku": sku, "label": f"{row['description']} quantity",
                           "po": p.qty, "invoice": i.qty, "detail": f"PO {p.qty}, invoice {i.qty}"})
        if d and i and i.qty > d.qty:
            row["flags"].append("over_delivered")
            issues.append({"field": "delivered", "sku": sku, "label": f"{row['description']} billed but not delivered",
                           "delivered": d.qty, "invoice": i.qty, "detail": f"delivered {d.qty}, invoiced {i.qty}"})
        if p and i and abs(i.unit_price - p.unit_price) > PRICE_TOLERANCE * p.unit_price:
            row["flags"].append("price")
            issues.append({"field": "unit_price", "sku": sku, "label": f"{row['description']} unit price",
                           "po": p.unit_price, "invoice": i.unit_price,
                           "detail": f"PO {inv.currency} {p.unit_price:,.2f}, invoice {inv.currency} {i.unit_price:,.2f}",
                           "impact": round((i.unit_price - p.unit_price) * i.qty, 2)})
        result["rows"].append(row)

    po_total = round(sum(l.total for l in order.lines), 2)
    if order.currency != inv.currency:
        issues.append({"field": "currency", "label": "Currency", "po": order.currency, "invoice": inv.currency,
                       "detail": f"PO in {order.currency}, invoice in {inv.currency}"})
    result["po_total"] = po_total
    result["invoice_total"] = round(inv.amount, 2)
    result["discrepancies"] = issues
    if inv.correction == "sent":
        # Fixed on our side, but the customer still has to re-approve it and
        # it waits for their next payment run.
        result["status"] = "AWAITING_APPROVAL"
        result["note"] = inv.correction_note
        days = HOLD_DAYS_CORRECTION_SENT
    elif issues and inv.correction != "accepted":
        result["status"] = "MISMATCH"
        days = HOLD_DAYS_UNRESOLVED
    else:
        result["status"] = "MATCHED"
        days = 0
    result["blocks_payment"] = days > 0
    result["expected_delay_days"] = days
    return result


def open_mismatches(company: Company) -> List[Dict[str, Any]]:
    out = []
    for inv in company.open_invoices():
        if inv.order_id and inv.lines:
            m = three_way_match(company, inv.id)
            if m["status"] == "MISMATCH":
                out.append(m)
    return out


def draft_correction(company: Company, invoice_id: str) -> Optional[Dict[str, Any]]:
    """Corrected invoice + covering note to the customer's registered accounts
    contact. Issuing it is a T1 action: templated, to a contact on file."""
    m = three_way_match(company, invoice_id)
    if m["status"] != "MISMATCH" or m["correction"]:
        return None
    inv = company.invoice(invoice_id)
    order = company.order(inv.order_id)
    fixes = [f"- {d['label']}: {d['detail']} — corrected to match your PO." for d in m["discrepancies"]]
    body = (
        f"Dear {inv.customer} accounts team,\n\n"
        f"Before your three-way match flags it: invoice {inv.id} for {order.po_number} contains an error on our side.\n"
        + "\n".join(fixes)
        + f"\n\nPlease use corrected invoice {inv.id}-C1 for {inv.currency} {m['po_total']:,.2f} "
          f"(previously {inv.currency} {inv.amount:,.2f}); it matches your PO and delivery note {m['delivery_id']}. "
          "Apologies for the trouble.\n\n"
          f"Regards,\n{company.profile.owner}\n{company.profile.legal_name}"
    )
    return {"invoice_id": invoice_id, "to": company.customer_contacts.get(inv.customer),
            "subject": f"Corrected invoice {inv.id}-C1 for {order.po_number}", "body": body,
            "new_total": m["po_total"], "old_total": inv.amount}
