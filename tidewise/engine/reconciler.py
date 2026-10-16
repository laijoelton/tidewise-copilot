"""
Deterministic reconciler: compares a shipping instruction with a bill of
lading field by field, after normalising entities (Sdn Bhd / Sdn. Bhd.,
L.L.C. / LLC), ports (Port Klang / Port Kelang, city + country forms) and
"SAME AS CONSIGNEE".

Returns {"status": OK | MISMATCH | NEEDS_REVIEW, "review_reason",
"has_defect", "defect_fields"}; NEEDS_REVIEW covers wrong document type,
missing document, unreadable document and missing (placeholder) value.
"""


import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .extractor import ExtractedDocFields
from .documents import AttachmentData


def normalize_text(s: Optional[str]) -> str:
    if not s:
        return ""
    # Strip punctuation and collapse whitespace
    s = re.sub(r"[^\w\s]", " ", s.upper())
    return " ".join(s.split())


def normalize_entity(s: Optional[str]) -> str:
    if not s:
        return ""
    clean = normalize_text(s)
    # Strip common legal suffixes (including period-separated variants like L.L.C., P.T.E. L.T.D.)
    legal_suffixes = [
        r"\bSDN\s*BHD\b", r"\bBHD\b", r"\bP\s*T\s*E\s*L\s*T\s*D\b", r"\bPTE\s*LTD\b", r"\bL\s*T\s*D\b", r"\bLTD\b",
        r"\bLIMITED\b", r"\bL\s*L\s*C\b", r"\bLLC\b", r"\bFZ\s*L\s*L\s*C\b", r"\bFZ\s*LLC\b", r"\bINC\b",
        r"\bCO\s*LTD\b", r"\bCOMPANY\b", r"\bCORP\b", r"\bCORPORATION\b",
        r"\bGMBH\b", r"\bS\s*A\b", r"\bB\s*V\b", r"\bPVT\b"
    ]
    for suf in legal_suffixes:
        clean = re.sub(suf, " ", clean)
    return " ".join(clean.split())


def normalize_port(s: Optional[str]) -> str:
    if not s:
        return ""
    clean = normalize_text(s)
    # Port synonyms mapping
    synonyms = {
        "PORT KELANG": "PORT KLANG",
        "KELANG": "PORT KLANG",
        "KLANG": "PORT KLANG",
        "MYPKG": "PORT KLANG",
        "TANJUNG PELEPAS": "TANJUNG PELEPAS",
        "PTP": "TANJUNG PELEPAS",
        "MYTPP": "TANJUNG PELEPAS",
        "JAWAHARLAL NEHRU": "NHAVA SHEVA",
        "JNPT": "NHAVA SHEVA",
        "INNSA": "NHAVA SHEVA"
    }
    for k, v in synonyms.items():
        if k in clean:
            clean = clean.replace(k, v)
    return clean


def extract_city(port_str: str) -> str:
    # Extracts primary city before comma or code
    first = port_str.split(",")[0].strip()
    return re.sub(r"\([A-Z0-9]+\)", "", first).strip()


PAREN_LOCODE = re.compile(r"\(\s*[A-Z0-9]{3,6}\s*\)", re.IGNORECASE)


def port_parts(s: Optional[str]) -> Tuple[frozenset, frozenset]:
    """Splits a port into (city tokens, all tokens), dropping any UN/LOCODE in brackets."""
    if not s:
        return frozenset(), frozenset()
    stripped = PAREN_LOCODE.sub(" ", s)
    if not stripped.strip():
        stripped = s
    full = frozenset(normalize_port(stripped).split())
    city = frozenset(normalize_port(stripped.split(",")[0]).split())
    return (city or full), full


def ports_match(a: Optional[str], b: Optional[str]) -> bool:
    """True when both entries name the same port.

    Each side's city must be contained in the other's full name, so a document may add the
    country or a UN/LOCODE ("JEBEL ALI" vs "JEBEL ALI, UAE") but not change the place itself
    ("SINGAPORE" vs "SINGAPORE CHANGED").
    """
    city_a, full_a = port_parts(a)
    city_b, full_b = port_parts(b)
    if not full_a or not full_b:
        return full_a == full_b
    return city_a <= full_b and city_b <= full_a


WEIGHT_TOLERANCE_KG = 0.5  # below 1 kg so a one-kilo difference is still reported


def select_documents(attachments: List[AttachmentData]) -> Tuple[Optional[AttachmentData], Optional[AttachmentData], bool]:
    """Picks the SI and the draft BL out of an email's attachments.

    Returns (si, bl, ambiguous). `ambiguous` is True when two or more files claim the same
    role, e.g. a second copy of the BL — the system cannot tell which one is authoritative.
    """
    si_candidates: List[AttachmentData] = []
    bl_candidates: List[AttachmentData] = []
    for att in attachments:
        fn = att.filename.upper()
        if "_SI" in fn or "SI_" in fn or att.detected_doc_type == "SI":
            si_candidates.append(att)
        elif "_BL" in fn or "BL_" in fn or att.detected_doc_type == "BL":
            bl_candidates.append(att)

    si = si_candidates[0] if si_candidates else None
    bl = bl_candidates[0] if bl_candidates else None
    if len(attachments) == 2 and (si is None or bl is None):
        si = si or next((d for d in attachments if d.detected_doc_type == "SI"), attachments[0])
        bl = bl or next((d for d in attachments if d.detected_doc_type == "BL"), attachments[1] if attachments[0] is si else attachments[0])

    ambiguous = len(si_candidates) > 1 or len(bl_candidates) > 1
    return si, bl, ambiguous


class DocumentReconciler:
    def reconcile(
        self,
        email_id: Any,
        category: Any = "BL_COMPARISON",
        si_att: Optional[AttachmentData] = None,
        bl_att: Optional[AttachmentData] = None,
        si_fields: Optional[ExtractedDocFields] = None,
        bl_fields: Optional[ExtractedDocFields] = None,
        draft_request: bool = False,
        ambiguous_documents: bool = False,
    ) -> Dict[str, Any]:
        """Reconciles an email case and returns the submission entry."""
        # Convenience: support reconcile(si_fields, bl_fields)
        if isinstance(email_id, ExtractedDocFields):
            si_fields = email_id
            bl_fields = category if isinstance(category, ExtractedDocFields) else bl_fields
            email_id = "reconcile_test"
            category = "BL_COMPARISON"
            if si_att is None and si_fields:
                si_att = AttachmentData(path="", filename="si_doc", extension=".pdf", detected_doc_type=si_fields.doc_type)
            if bl_att is None and bl_fields:
                bl_att = AttachmentData(path="", filename="bl_doc", extension=".pdf", detected_doc_type=bl_fields.doc_type)

        # 1. Non-comparison categories
        if category != "BL_COMPARISON":
            return {
                "category": category,
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }

        # 1b. Request to send the draft BL: nothing to compare yet, and nothing is missing
        if draft_request and si_att is None and bl_att is None:
            return {
                "category": "BL_COMPARISON",
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }

        # 2. Check missing attachments
        if si_att is None or bl_att is None:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_attachment",
                "has_defect": False,
                "defect_fields": []
            }

        # 3. Check unreadable/corrupt files
        if (
            si_att.is_corrupted
            or bl_att.is_corrupted
            or (si_fields and not si_fields.is_legible)
            or (bl_fields and not bl_fields.is_legible)
        ):
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "unreadable",
                "has_defect": False,
                "defect_fields": []
            }

        # 3b. Two files claim the same role (e.g. a second copy of the BL): a human must say which one counts
        if ambiguous_documents:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "wrong_doc_type",
                "has_defect": False,
                "defect_fields": []
            }

        # 4. Check wrong document type (disguised invoice, packing list, COO)
        wrong_types = ("INVOICE", "PACKING_LIST", "COO")
        if si_att.detected_doc_type in wrong_types or bl_att.detected_doc_type in wrong_types:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "wrong_doc_type",
                "has_defect": False,
                "defect_fields": []
            }

        # 5. Check missing values or placeholders
        if si_fields is None or bl_fields is None:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_value",
                "has_defect": False,
                "defect_fields": []
            }

        # A document that states the same field twice with different values cannot be read dependably
        if getattr(si_fields, "has_conflicting_value", False) or getattr(bl_fields, "has_conflicting_value", False):
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_value",
                "has_defect": False,
                "defect_fields": []
            }

        if si_fields.has_missing_placeholder or bl_fields.has_missing_placeholder:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_value",
                "has_defect": False,
                "defect_fields": []
            }

        # Verify all 7 fields have a non-None value in both SI and BL
        required_attrs = [
            "shipper", "consignee", "notify_party",
            "port_of_loading", "port_of_discharge",
            "container_count", "gross_weight_kg"
        ]
        for attr in required_attrs:
            if getattr(si_fields, attr) is None or getattr(bl_fields, attr) is None:
                return {
                    "category": "BL_COMPARISON",
                    "status": "NEEDS_REVIEW",
                    "review_reason": "missing_value",
                    "has_defect": False,
                    "defect_fields": []
                }

        # 6. Compare the 7 fields
        defect_fields = []

        # (1) Shipper
        if normalize_entity(si_fields.shipper) != normalize_entity(bl_fields.shipper):
            defect_fields.append("shipper")

        # (2) Consignee
        if normalize_entity(si_fields.consignee) != normalize_entity(bl_fields.consignee):
            defect_fields.append("consignee")

        # (3) Notify Party (resolving SAME AS CONSIGNEE)
        si_notify = si_fields.notify_party
        bl_notify = bl_fields.notify_party
        if normalize_text(si_notify) in ("SAME AS CONSIGNEE", "SAME AS ABOVE", "AS ABOVE"):
            si_notify = si_fields.consignee
        if normalize_text(bl_notify) in ("SAME AS CONSIGNEE", "SAME AS ABOVE", "AS ABOVE"):
            bl_notify = bl_fields.consignee

        if normalize_entity(si_notify) != normalize_entity(bl_notify):
            defect_fields.append("notify_party")

        # (4) Port of Loading
        if not ports_match(si_fields.port_of_loading, bl_fields.port_of_loading):
            defect_fields.append("port_of_loading")

        # (5) Port of Discharge
        if not ports_match(si_fields.port_of_discharge, bl_fields.port_of_discharge):
            defect_fields.append("port_of_discharge")

        # (6) Container Count
        if si_fields.container_count != bl_fields.container_count:
            defect_fields.append("container_count")

        # (7) Gross Weight (KG)
        if abs(si_fields.gross_weight_kg - bl_fields.gross_weight_kg) > WEIGHT_TOLERANCE_KG:
            defect_fields.append("gross_weight_kg")

        if defect_fields:
            return {
                "category": "BL_COMPARISON",
                "status": "MISMATCH",
                "review_reason": None,
                "has_defect": True,
                "defect_fields": defect_fields
            }
        else:
            return {
                "category": "BL_COMPARISON",
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }
