"""
Document loader: reads trade documents from disk into AttachmentData.

Supports .txt (UTF-8 / Latin-1 / CP1252), .pdf (pypdf, with corruption
trapping and scanned-page detection), .docx and .xlsx (library parsers with
a zipfile fallback), and detects the document type from content (SI, BL,
INVOICE, PACKING_LIST, COO) so a mislabelled file is caught.
"""


import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from pypdf import PdfReader
    from pypdf.errors import PdfStreamError
except ImportError:
    PdfReader = None
    PdfStreamError = Exception

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import docx
except ImportError:
    docx = None


@dataclass
class AttachmentData:
    path: str
    filename: str
    extension: str
    text: str = ""
    raw_bytes: bytes = b""
    is_corrupted: bool = False
    is_scanned: bool = False
    error_message: Optional[str] = None
    detected_doc_type: str = "UNKNOWN"  # "SI", "BL", "INVOICE", "PACKING_LIST", "COO", "UNKNOWN"


class DocumentLoader:
    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    def load_attachment(self, rel_path: str) -> AttachmentData:
        full_path = self.base_dir / rel_path
        filename = full_path.name
        ext = full_path.suffix.lower()

        if not full_path.exists():
            return AttachmentData(
                path=rel_path,
                filename=filename,
                extension=ext,
                is_corrupted=True,
                error_message=f"File not found: {rel_path}",
                detected_doc_type="UNKNOWN"
            )

        try:
            raw_bytes = full_path.read_bytes()
        except Exception as e:
            return AttachmentData(
                path=rel_path,
                filename=filename,
                extension=ext,
                is_corrupted=True,
                error_message=f"Read error: {e}",
                detected_doc_type="UNKNOWN"
            )

        att = AttachmentData(
            path=rel_path,
            filename=filename,
            extension=ext,
            raw_bytes=raw_bytes
        )

        if ext == ".txt":
            self._parse_txt(att, raw_bytes)
        elif ext == ".pdf":
            self._parse_pdf(att, full_path, raw_bytes)
        elif ext == ".docx":
            self._parse_docx(att, full_path, raw_bytes)
        elif ext in (".xlsx", ".xls"):
            self._parse_xlsx(att, full_path, raw_bytes)
        else:
            self._parse_txt(att, raw_bytes)

        att.detected_doc_type = self._detect_doc_type(att.text, filename)
        return att

    def _parse_txt(self, att: AttachmentData, raw_bytes: bytes) -> None:
        for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
            try:
                att.text = raw_bytes.decode(enc)
                return
            except UnicodeDecodeError:
                continue
        att.text = raw_bytes.decode("utf-8", errors="replace")

    def _parse_pdf(self, att: AttachmentData, full_path: Path, raw_bytes: bytes) -> None:
        if PdfReader is None:
            att.is_corrupted = True
            att.error_message = "pypdf not installed"
            return

        try:
            reader = PdfReader(str(full_path))
            pages_text = []
            for p in reader.pages:
                t = p.extract_text() or ""
                pages_text.append(t)
            combined = "\n".join(pages_text).strip()
            att.text = combined
            if len(combined) < 50:
                att.is_scanned = True
        except Exception as e:
            att.is_corrupted = True
            att.error_message = f"PDF parsing error: {e}"

    def _parse_docx(self, att: AttachmentData, full_path: Path, raw_bytes: bytes) -> None:
        if docx is not None:
            try:
                doc = docx.Document(str(full_path))
                paragraphs = [p.text for p in doc.paragraphs if p.text]
                for t in doc.tables:
                    for row in t.rows:
                        row_text = [c.text.strip() for c in row.cells if c.text.strip()]
                        if row_text:
                            paragraphs.append(" : ".join(row_text))
                att.text = "\n".join(paragraphs).strip()
                return
            except Exception:
                pass

        # Fallback XML parsing
        try:
            with zipfile.ZipFile(full_path) as z:
                xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            texts = []
            for elem in tree.iter():
                if elem.tag.endswith("}t") and elem.text:
                    texts.append(elem.text)
                elif elem.tag.endswith("}p"):
                    texts.append("\n")
            att.text = "".join(texts).strip()
        except Exception as e:
            att.is_corrupted = True
            att.error_message = f"DOCX parsing error: {e}"

    def _parse_xlsx(self, att: AttachmentData, full_path: Path, raw_bytes: bytes) -> None:
        if openpyxl is not None:
            try:
                wb = openpyxl.load_workbook(str(full_path), data_only=True)
                lines = []
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=True):
                        cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                        if cells:
                            lines.append(" : ".join(cells))
                att.text = "\n".join(lines).strip()
                return
            except Exception:
                pass

        # Fallback XML parsing
        try:
            with zipfile.ZipFile(full_path) as z:
                texts = []
                for name in z.namelist():
                    if name.startswith("xl/worksheets/sheet") or name == "xl/sharedStrings.xml":
                        tree = ET.fromstring(z.read(name))
                        for elem in tree.iter():
                            if elem.tag.endswith("}t") and elem.text:
                                texts.append(elem.text)
                att.text = "\n".join(texts).strip()
        except Exception as e:
            att.is_corrupted = True
            att.error_message = f"XLSX parsing error: {e}"

    def _detect_doc_type(self, text: str, filename: str) -> str:
        """Determines document type based on content first, then filename fallback."""
        upper = text.upper()
        # Content header checks
        if "COMMERCIAL INVOICE" in upper or "INVOICE NO" in upper:
            return "INVOICE"
        if "PACKING LIST" in upper or "PACKING LIST NO" in upper:
            return "PACKING_LIST"
        if "CERTIFICATE OF ORIGIN" in upper or "FORM D" in upper or "FORM E" in upper:
            return "COO"
        if (
            "BILL OF LADING INSTRUCTION" in upper
            or "BL INSTRUCTION" in upper
            or "SHIPPING INSTRUCTION" in upper
            or "SHIPPING INSTRUCTIONS" in upper
            or "SHIPPER'S INSTRUCTION" in upper
        ):
            return "SI"
        if "BILL OF LADING" in upper or "DRAFT BL" in upper or "BILL OF LADING (DRAFT)" in upper:
            return "BL"

        # Filename fallback
        upper_fn = filename.upper()
        if "_SI" in upper_fn or "SI_" in upper_fn or "SHIPPING_INSTRUCTION" in upper_fn:
            return "SI"
        if "_BL" in upper_fn or "BL_" in upper_fn or "BILL_OF_LADING" in upper_fn:
            return "BL"
        return "UNKNOWN"
