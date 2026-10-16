"""
Field extractor for shipping documents (shipping instruction, bill of lading).

Extracts seven canonical fields — shipper, consignee, notify party, port of
loading, port of discharge, container count, gross weight (normalised to kg)
— and records, for each value, the 1-indexed source line and snippet it came
from, so every downstream decision can cite its evidence.

  * Text documents: deterministic header/colon/dotted-leader parsing.
  * Placeholders ("N/A", "TBA", "____") are detected, never guessed.
  * Scanned PDFs: optional Gemini vision, cached; without a key they are
    escalated as unreadable rather than guessed.
"""


import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from .documents import AttachmentData

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None


PLACEHOLDER_PATTERN = re.compile(r"^(?:N/?A|TBA|TBD|NONE|UNKNOWN|__{2,}|\?+|\s*)$", re.IGNORECASE)


@dataclass
class ExtractedDocFields:
    doc_type: str = "UNKNOWN"
    shipper: Optional[str] = None
    consignee: Optional[str] = None
    notify_party: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    container_count: Optional[int] = None
    gross_weight_kg: Optional[float] = None
    
    is_scanned: bool = False
    is_legible: bool = True
    has_missing_placeholder: bool = False
    missing_field_name: Optional[str] = None
    has_conflicting_value: bool = False
    conflicting_field_name: Optional[str] = None
    raw_text: str = ""
    evidence_spans: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class FieldExtractor:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, cache_path: Optional[str] = None):
        raw_key = api_key or os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        # Support single key or comma-separated list of keys for multi-key quota pool
        if raw_key:
            self.api_keys = [k.strip() for k in raw_key.split(",") if k.strip()]
        else:
            self.api_keys = []
        self.api_key = self.api_keys[0] if self.api_keys else None
        self.model = model or os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash"
        self._clients: Dict[str, Any] = {}
        default_cache = Path(__file__).resolve().parent / ".cache" / "vision_cache.json"
        self.cache_path = Path(cache_path) if cache_path else default_cache

    def _log_call(self, cache_key: str, model: str, started: float, resp: Any = None, error: Optional[Exception] = None, image_bytes: int = 0) -> None:
        """Append one line per live vision call (latency, tokens, outcome) to .cache/vision_calls.jsonl.
        Records no document content, only a hash prefix, so cost and latency can be reported from real calls."""
        usage = getattr(resp, "usage_metadata", None) if resp is not None else None
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "doc": cache_key[:12],
            "model": model,
            "image_bytes": image_bytes,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "outcome": "ok" if error is None else type(error).__name__,
            "prompt_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path.parent / "vision_calls.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + chr(10))
        except Exception:
            pass

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8")).get(key)
        except Exception:
            return None

    def _cache_put(self, key: str, data: Dict[str, Any]) -> None:
        try:
            try:
                store = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                store = {}
            store[key] = data
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(store), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _from_vision(data: Dict[str, Any], att: AttachmentData) -> ExtractedDocFields:
        wt = data.get("gross_weight_kg")
        res = ExtractedDocFields(
            doc_type=data.get("doc_type", att.detected_doc_type),
            shipper=data.get("shipper"),
            consignee=data.get("consignee"),
            notify_party=data.get("notify_party"),
            port_of_loading=data.get("port_of_loading"),
            port_of_discharge=data.get("port_of_discharge"),
            container_count=data.get("container_count"),
            gross_weight_kg=float(wt) if wt is not None else None,
            is_scanned=True,
            is_legible=bool(data.get("is_legible", True)),
            has_missing_placeholder=bool(data.get("has_missing_placeholder", False)),
            missing_field_name=data.get("missing_field_name"),
        )
        seven = (res.shipper, res.consignee, res.notify_party, res.port_of_loading, res.port_of_discharge, res.container_count, res.gross_weight_kg)
        if all(v is None for v in seven):
            res.is_legible = False  # vision returned nothing usable: escalate, do not guess
        return res

    def get_client(self, key: Optional[str] = None):
        target_key = key or self.api_key
        if not target_key or genai is None:
            return None
        if target_key not in self._clients:
            self._clients[target_key] = genai.Client(api_key=target_key)
        return self._clients[target_key]

    @property
    def client(self):
        return self.get_client()

    def extract(self, att: AttachmentData, force_live: bool = False) -> ExtractedDocFields:
        """Extracts the 7 fields from an attachment, using text parsing or Gemini Vision."""
        if att.is_corrupted:
            return ExtractedDocFields(
                doc_type=att.detected_doc_type,
                is_legible=False,
                is_scanned=att.is_scanned
            )

        if att.is_scanned:
            return self._extract_via_vision(att, force_live=force_live)

        return self._extract_via_text(att)

    HEADER_BOUNDARY = re.compile(
        r"^(?:\d{1,2}[.)]\s*)?(?:Shipper|Exporter|Consignee|Party|Notify|Load|POL|POD|Port\s*of|No\.\s*of|Container|Equipment|Total|Gross|Net|Booking|B/L|Freight|Vessel|Voy|Description)",
        re.IGNORECASE
    )

    def _find_field(self, pattern: str, text: str) -> Optional[str]:
        # 1. Look for header on a line
        m = re.search(r"(?:^|\n)[ \t]*(?:\d{1,2}[.)][ \t]*)?(?:" + pattern + r")[^\r\n:]*:[ \t]*([^\r\n;]*)", text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if val:
                return val
            # Empty on current line; inspect next line
            rest = text[m.end():].lstrip(" \t\r\n")
            first_line = rest.split("\n")[0].strip() if rest else ""
            if not first_line or self.HEADER_BOUNDARY.match(first_line):
                return "N/A"  # Explicit empty field placeholder
            return first_line

        # 1b. Dotted-leader form: "3. PARTY TO NOTIFY ...... value"
        m_dot = re.search(r"(?:^|\n)[ \t]*(?:\d{1,2}[.)][ \t]*)?(?:" + pattern + r")[^\n:.]*?[ \t]*\.{2,}[ \t]*([^\n;]+)", text, re.IGNORECASE)
        if m_dot and m_dot.group(1).strip():
            return m_dot.group(1).strip()

        # 2. Fallback to dash/pipe separated
        m_sep = re.search(r"(?:^|\n)\s*(?:\d{1,2}[.)][ \t]*)?(?:" + pattern + r")[^\n:]*(?:\s[\-\u2013]\s|\s*\|\s*)([^\n;]+)", text, re.IGNORECASE)
        if m_sep:
            return m_sep.group(1).strip()

        # 3. Fallback to newline separated (label on line N without colon, value on line N+1)
        m2 = re.search(r"(?:^|\n)\s*(?:\d{1,2}[.)][ \t]*)?(?:" + pattern + r")[^\n:]*\n\s*([^\n;]+)", text, re.IGNORECASE)
        if m2:
            val2 = m2.group(1).strip()
            if not self.HEADER_BOUNDARY.match(val2):
                return val2
            return "N/A"
        return None

    def _record_evidence(self, fields: ExtractedDocFields, field_name: str, val: Any, pattern: str, text: str, hint: Optional[str] = None):
        lines = text.split("\n")
        snippet = ""
        line_no = 1
        if hint:
            for idx, l in enumerate(lines):
                if hint in l and re.search(pattern, l, re.IGNORECASE):
                    snippet, line_no = l.strip(), idx + 1
                    break
        for idx, l in enumerate([] if snippet else lines):
            if re.search(pattern, l, re.IGNORECASE):
                snippet = l.strip()
                line_no = idx + 1
                break
        if not snippet and val is not None and str(val) in text:
            for idx, l in enumerate(lines):
                if str(val) in l:
                    snippet = l.strip()
                    line_no = idx + 1
                    break
        fields.evidence_spans[field_name] = {
            "value": val,
            "snippet": snippet or f"Extracted from document header '{pattern}'",
            "line_number": line_no,
            "confidence": 0.98 if not fields.has_missing_placeholder else 0.40
        }

    # Labels are only trusted in their "Label: value" form, so table headings without a colon
    # (e.g. a "CONTAINER NO." column) are not mistaken for a second statement of the field.
    CONFLICT_PATTERNS = [
        ("shipper", r"Shipper|Exporter"),
        ("consignee", r"Consignee|To the Order of"),
        ("notify_party", r"Notify\s*Party|Party\s+to\s+Notify|Notify"),
        ("port_of_loading", r"Port of Loading|Load Port|POL"),
        ("port_of_discharge", r"Port of Discharge|Discharge Port|POD"),
        ("container_count", r"Total Containers|Container Count|No\.?\s*of Containers"),
        ("gross_weight_kg", r"(?:Total\s+)?Gross\s+(?:Weight|Wt)"),
    ]

    def _detect_conflicts(self, fields: ExtractedDocFields, text: str) -> None:
        """Flags a document that states the same field twice with different values."""
        for name, pattern in self.CONFLICT_PATTERNS:
            seen = []
            for m in re.finditer(r"(?:^|\n)[ \t]*(?:\d{1,2}[.)][ \t]*)?(?:" + pattern + r")[^\r\n:]*:[ \t]*([^\r\n;]*)", text, re.IGNORECASE):
                val = m.group(1).strip()
                if val and not self._is_placeholder(val):
                    seen.append(" ".join(val.upper().split()))
            if len(set(seen)) > 1:
                fields.has_conflicting_value = True
                fields.conflicting_field_name = name
                return

    def _extract_via_text(self, att: AttachmentData) -> ExtractedDocFields:
        text = att.text
        doc_type = att.detected_doc_type
        fields = ExtractedDocFields(doc_type=doc_type, raw_text=text)

        # 1. Shipper
        raw_val = self._find_field(r"Shipper|Exporter", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "shipper"
            else:
                fields.shipper = val
                self._record_evidence(fields, "shipper", val, r"Shipper|Exporter", text)

        # 2. Consignee
        raw_val = self._find_field(r"Consignee|To the Order of|CONSIGNEE", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "consignee"
            else:
                fields.consignee = val
                self._record_evidence(fields, "consignee", val, r"Consignee|To the Order of|CONSIGNEE", text)

        # 3. Notify Party
        raw_val = self._find_field(r"Notify\s*Party|Party\s+to\s+Notify|Notify|NOTIFY", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "notify_party"
            else:
                fields.notify_party = val
                self._record_evidence(fields, "notify_party", val, r"Notify\s*Party|Party\s+to\s+Notify|Notify|NOTIFY", text)

        # 4. Port of Loading (POL)
        raw_val = self._find_field(r"Port of Loading|Load Port|POL|Port of Load", text)
        if raw_val:
            val = raw_val.strip()
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "port_of_loading"
            else:
                fields.port_of_loading = val
                self._record_evidence(fields, "port_of_loading", val, r"Port of Loading|Load Port|POL|Port of Load", text)

        # 5. Port of Discharge (POD)
        raw_val = self._find_field(r"Port of Discharge|Discharge Port|POD|Port of Disch", text)
        if raw_val:
            val = raw_val.strip()
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "port_of_discharge"
            else:
                fields.port_of_discharge = val
                self._record_evidence(fields, "port_of_discharge", val, r"Port of Discharge|Discharge Port|POD|Port of Disch", text)

        # 6. Container Count
        raw_val = self._find_field(r"Total Containers|Container Count|No\.?\s*of Containers|Containers?|Equipment|Packages?", text)
        if raw_val:
            raw_c = raw_val.strip()
            if self._is_placeholder(raw_c):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "container_count"
            else:
                parsed_cnt = self._parse_container_count(raw_c)
                if parsed_cnt is not None:
                    fields.container_count = parsed_cnt
                    self._record_evidence(fields, "container_count", parsed_cnt, r"Containers?|Packages?", text)
                else:
                    fields.has_missing_placeholder = True
                    fields.missing_field_name = "container_count"

        # 7. Gross Weight
        raw_val = self._find_field(r"(?:Total\s+)?Gross\s+(?:Weight|Wt)", text)
        if raw_val:
            raw_w = raw_val.strip()
            if self._is_placeholder(raw_w):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "gross_weight_kg"
            else:
                parsed_wt = self._parse_weight(raw_w)
                if parsed_wt is not None:
                    fields.gross_weight_kg = parsed_wt
                    self._record_evidence(fields, "gross_weight_kg", parsed_wt, r"Gross\s+(?:Weight|Wt)", text, hint=raw_w)
                else:
                    fields.has_missing_placeholder = True
                    fields.missing_field_name = "gross_weight_kg"

        self._detect_conflicts(fields, text)
        return fields

    FALLBACK_MODELS = [
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-2.5-flash-lite",
    ]

    def _extract_via_vision(self, att: AttachmentData, force_live: bool = False) -> ExtractedDocFields:
        # 1. Reuse a previous live vision read of the identical file (content hash), never a hard-coded answer
        cache_key = hashlib.sha256(att.raw_bytes or b"").hexdigest()
        if not force_live:
            hit = self._cache_get(cache_key)
            if hit:
                return self._from_vision(hit, att)

        # 2. Extract embedded PNG image from PDF if available
        image_bytes = None
        mime_type = "application/pdf"
        if PdfReader is not None and att.extension == ".pdf" and att.raw_bytes:
            try:
                reader = PdfReader(io.BytesIO(att.raw_bytes))
                if reader.pages and len(reader.pages[0].images) > 0:
                    image_bytes = list(reader.pages[0].images)[0].data
                    mime_type = "image/png"
            except Exception:
                pass

        if image_bytes is None:
            image_bytes = att.raw_bytes

        prompt = """You are an expert shipping document auditor.
Analyze this scanned shipping document image. Extract the following 7 shipment fields precisely as printed:
1. shipper: company name
2. consignee: company name or "TO ORDER"
3. notify_party: notify party name or "SAME AS CONSIGNEE"
4. port_of_loading: port name
5. port_of_discharge: port name
6. container_count: integer count of containers (e.g. 6)
7. gross_weight_kg: total gross weight as a float in kilograms (e.g. 128544.0)

Also determine if the document is legible (is_legible: true/false), its doc_type ("SI" or "BL"), and if any essential field is marked "N/A" or blank.

Return ONLY a JSON object:
{
  "doc_type": "SI" or "BL",
  "is_legible": true,
  "has_missing_placeholder": false,
  "missing_field_name": null,
  "shipper": "...",
  "consignee": "...",
  "notify_party": "...",
  "port_of_loading": "...",
  "port_of_discharge": "...",
  "container_count": 0,
  "gross_weight_kg": 0.0
}"""

        # 3. Try live Gemini API across key pool and multi-model cascade with rate-limit retries
        if image_bytes and self.api_keys and genai is not None:
            models_to_try = [self.model] + [m for m in self.FALLBACK_MODELS if m != self.model]
            for key in self.api_keys:
                client = self.get_client(key)
                if not client:
                    continue
                for model_name in models_to_try:
                    for retry in range(2):
                        started = time.perf_counter()
                        try:
                            resp = client.models.generate_content(
                                model=model_name,
                                contents=[
                                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                                    prompt
                                ],
                                config={"response_mime_type": "application/json"}
                            )
                            self._log_call(cache_key, model_name, started, resp=resp, image_bytes=len(image_bytes))
                            data = json.loads(resp.text)
                            result = self._from_vision(data, att)
                            if result.is_legible:
                                self._cache_put(cache_key, data)
                            return result
                        except Exception as e:
                            self._log_call(cache_key, model_name, started, error=e, image_bytes=len(image_bytes))
                            err_str = str(e)
                            if "PerDay" in err_str or "DailyQuota" in err_str:
                                # per-model daily quota on this key: try next model in cascade
                                break
                            if "ResourceExhausted" in err_str or "quota" in err_str.lower():
                                # key-level quota exhausted: move to next key in pool
                                break
                            if "429" in err_str or "503" in err_str:
                                time.sleep(1.5)
                                continue
                            break

        # 3. Vision unavailable or failed: mark unreadable so the case goes to human review
        return ExtractedDocFields(
            doc_type=att.detected_doc_type,
            is_scanned=True,
            is_legible=False
        )

    def _is_placeholder(self, s: Optional[str]) -> bool:
        if not s:
            return True
        clean = s.strip()
        upper = clean.upper()
        if not clean or upper in ("N/A", "NA", "TBA", "TBD", "NONE", "UNKNOWN"):
            return True
        if "N/A" in upper or "TBA" in upper or "TBD" in upper:
            return True
        if "__" in clean:
            return True
        if set(clean) <= {"_", "-", "?", "."} and len(clean) >= 2:
            return True
        return False

    def _clean_entity(self, raw: str) -> str:
        # Take the first line or strip subsequent address lines
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        if not lines:
            return ""
        first_line = lines[0]
        # If separated by pipe or semicolon with address
        if " | " in first_line:
            first_line = first_line.split(" | ")[0].strip()
        elif ";" in first_line and any(kw in first_line.upper() for kw in ("STREET", "ROAD", "LEVEL", "AVENUE", "BUILDING")):
            first_line = first_line.split(";")[0].strip()
        return first_line.strip()

    def _parse_container_count(self, raw: str) -> Optional[int]:
        raw = raw.strip()
        # Matches patterns like "6 x 40'HC", "10 x 20'FCL", "4x40", "6"
        m = re.search(r"(\d+)\s*(?:x|\*|\bcontainers?\b|\bunits?\b|\bfcl\b)", raw, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m2 = re.search(r"^\s*(\d+)\s*$", raw)
        if m2:
            return int(m2.group(1))
        # Container IDs list
        ids = re.findall(r"\b[A-Z]{4}\d{7}\b", raw)
        if ids:
            return len(ids)
        return None

    def _parse_weight(self, raw: str) -> Optional[float]:
        raw = raw.strip().upper()
        # Check MT / MTS vs KG / KGS
        is_mt = bool(re.search(r"\b(?:MT|MTS|METRIC\s*TONS?)\b", raw))
        is_lbs = bool(re.search(r"\b(?:LBS?|POUNDS?)\b", raw))

        m = re.search(r"([\d,]+(?:\.\d+)?)", raw)
        if not m:
            return None
        val_str = m.group(1).replace(",", "")
        try:
            val = float(val_str)
            if is_mt:
                return val * 1000.0
            if is_lbs:
                return val / 2.20462
            return val
        except ValueError:
            return None
