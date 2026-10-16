"""Trade-document check, built on the deterministic extractor and
reconciler in tidewise.engine.

What Tidewise adds is the financial consequence: for a shipment sold under a
letter of credit, a discrepancy between the shipping instruction and the bill
of lading means the bank refuses the presentation, so the receivable it
secures is pushed out by the amend-and-re-present cycle. The forecaster uses
that delay; the agent uses the evidence to draft the amendment request.
"""

from typing import Any, Dict

from .engine.documents import AttachmentData
from .engine.extractor import FieldExtractor
from .engine.reconciler import DocumentReconciler
from .models import Company

# Refusal notice + carrier amendment + re-presentation + bank examination
# (UCP 600 Art. 14(b) gives the bank up to 5 banking days per examination).
LC_DISCREPANCY_DELAY_DAYS = 21

FIELD_LABELS = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify party",
    "port_of_loading": "Port of loading",
    "port_of_discharge": "Port of discharge",
    "container_count": "Container count",
    "gross_weight_kg": "Gross weight (kg)",
}

_extractor = FieldExtractor(api_key="")   # text documents only: never calls a model
_reconciler = DocumentReconciler()


def check_shipment(company: Company, shipment_id: str) -> Dict[str, Any]:
    shipment = company.shipment(shipment_id)
    if shipment is None:
        raise KeyError(shipment_id)
    invoice = company.invoice(shipment.invoice_id)

    si = _extractor.extract(AttachmentData(path="", filename=f"SI-{shipment_id}.txt", extension=".txt",
                                           text=shipment.si_text, detected_doc_type="SI"))
    bl = _extractor.extract(AttachmentData(path="", filename=f"BL-{shipment_id}.txt", extension=".txt",
                                           text=shipment.bl_text, detected_doc_type="BL"))
    verdict = _reconciler.reconcile(si, bl)

    discrepancies = []
    for f in verdict["defect_fields"]:
        si_ev, bl_ev = si.evidence_spans.get(f, {}), bl.evidence_spans.get(f, {})
        discrepancies.append({
            "field": f,
            "label": FIELD_LABELS.get(f, f),
            "si_value": getattr(si, f),
            "bl_value": getattr(bl, f),
            "si_evidence": {"line": si_ev.get("line_number"), "text": si_ev.get("snippet")},
            "bl_evidence": {"line": bl_ev.get("line_number"), "text": bl_ev.get("snippet")},
        })

    lc = invoice is not None and invoice.terms == "LC"
    blocks_payment = lc and verdict["status"] != "OK"
    return {
        "shipment_id": shipment_id,
        "invoice_id": shipment.invoice_id,
        "vessel": shipment.vessel,
        "status": verdict["status"],
        "discrepancies": discrepancies,
        "lc": shipment.lc_terms or None,
        "blocks_lc_payment": blocks_payment,
        "expected_delay_days": LC_DISCREPANCY_DELAY_DAYS if blocks_payment else 0,
        "engine": "tidewise.engine extractor + reconciler (deterministic, line-level evidence)",
    }


def draft_amendment_request(company: Company, shipment_id: str) -> Dict[str, Any]:
    """Drafts the carrier/forwarder amendment email. Drafting is tier 1:
    nothing leaves the business until the owner (or policy) sends it."""
    result = check_shipment(company, shipment_id)
    shipment = company.shipment(shipment_id)
    if not result["discrepancies"]:
        return {"shipment_id": shipment_id, "needed": False}
    lines = [f"- {d['label']}: draft B/L shows \"{d['bl_value']}\"; our shipping instruction "
             f"(line {d['si_evidence']['line']}) states \"{d['si_value']}\"." for d in result["discrepancies"]]
    body = (
        f"Dear PortLink team,\n\n"
        f"Please amend draft B/L for {shipment.vessel} (our ref {shipment_id}) before issuing the original:\n"
        + "\n".join(lines)
        + f"\n\nThis shipment is under letter of credit {(shipment.lc_terms or {}).get('lc_number', '')}; "
          "the B/L must agree with the shipping instruction and packing list or the bank will refuse "
          "the presentation. Please confirm the corrected draft today.\n\n"
          f"Regards,\n{company.profile.owner}\n{company.profile.legal_name}"
    )
    return {
        "shipment_id": shipment_id,
        "needed": True,
        "to": "docs@portlink-forwarding.example",
        "subject": f"URGENT B/L amendment — {shipment_id} / {shipment.vessel}",
        "body": body,
    }
