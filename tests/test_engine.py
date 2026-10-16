"""Tests for tidewise.engine: document loading, field extraction with
evidence, reconciliation and the Merkle log."""

import tempfile
import unittest
from pathlib import Path

from tidewise.engine.documents import AttachmentData, DocumentLoader
from tidewise.engine.extractor import ExtractedDocFields, FieldExtractor
from tidewise.engine.merkle import MerkleAuditLog
from tidewise.engine.ratelimit import RateLimiter
from tidewise.engine.reconciler import DocumentReconciler

SI = """SHIPPING INSTRUCTION
Shipper: KAYU MAKMUR EXPORTS SDN BHD, MUAR, MALAYSIA
Consignee: NORDHAVN HOME AS, OSLO, NORWAY
Notify Party: SAME AS CONSIGNEE
Port of Loading: PORT KLANG, MALAYSIA
Port of Discharge: OSLO, NORWAY
No. of Containers: 3 x 40HC
Gross Weight: 24,310 KGS
"""


def fields(**kw) -> ExtractedDocFields:
    base = dict(doc_type="SI", shipper="KAYU MAKMUR EXPORTS SDN BHD", consignee="NORDHAVN HOME AS",
                notify_party="NORDHAVN HOME AS", port_of_loading="PORT KLANG", port_of_discharge="OSLO",
                container_count=3, gross_weight_kg=24310.0, is_legible=True)
    base.update(kw)
    return ExtractedDocFields(**base)


class TestExtractor(unittest.TestCase):
    def setUp(self):
        self.fx = FieldExtractor(api_key="")

    def test_fields_and_line_evidence(self):
        f = self.fx.extract(AttachmentData(path="", filename="si.txt", extension=".txt", text=SI, detected_doc_type="SI"))
        self.assertEqual(f.container_count, 3)
        self.assertEqual(f.gross_weight_kg, 24310.0)
        self.assertEqual(f.port_of_loading, "PORT KLANG, MALAYSIA")
        self.assertEqual(f.evidence_spans["gross_weight_kg"]["line_number"], 8)

    def test_placeholder_detected_not_guessed(self):
        text = SI.replace("Gross Weight: 24,310 KGS", "Gross Weight: TBA")
        f = self.fx.extract(AttachmentData(path="", filename="si.txt", extension=".txt", text=text, detected_doc_type="SI"))
        self.assertTrue(f.has_missing_placeholder)
        self.assertIsNone(f.gross_weight_kg)

    def test_scanned_without_key_is_unreadable(self):
        f = self.fx.extract(AttachmentData(path="", filename="scan.pdf", extension=".pdf", is_scanned=True,
                                           detected_doc_type="BL"))
        self.assertFalse(f.is_legible)


class TestReconciler(unittest.TestCase):
    def setUp(self):
        self.r = DocumentReconciler()

    def test_normalised_variants_match(self):
        bl = fields(doc_type="BL", shipper="KAYU MAKMUR EXPORTS SDN. BHD.", notify_party="SAME AS CONSIGNEE",
                    port_of_loading="PORT KELANG", port_of_discharge="OSLO, NORWAY")
        self.assertEqual(self.r.reconcile(fields(), bl)["status"], "OK")

    def test_weight_mismatch(self):
        res = self.r.reconcile(fields(), fields(doc_type="BL", gross_weight_kg=24130.0))
        self.assertEqual(res["status"], "MISMATCH")
        self.assertEqual(res["defect_fields"], ["gross_weight_kg"])

    def test_container_mismatch(self):
        res = self.r.reconcile(fields(), fields(doc_type="BL", container_count=2))
        self.assertIn("container_count", res["defect_fields"])

    def test_unreadable_escalates(self):
        res = self.r.reconcile(fields(), fields(doc_type="BL", is_legible=False))
        self.assertEqual(res["status"], "NEEDS_REVIEW")


class TestDocumentLoader(unittest.TestCase):
    def test_txt_loaded_and_typed(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "si.txt").write_text(SI, encoding="utf-8")
            att = DocumentLoader(d).load_attachment("si.txt")
            self.assertIn("Gross Weight", att.text)
            self.assertEqual(att.detected_doc_type, "SI")

    def test_missing_file_is_corrupted_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(DocumentLoader(d).load_attachment("nope.pdf").is_corrupted)


class TestMerkle(unittest.TestCase):
    def test_proofs_and_chain(self):
        log = MerkleAuditLog()
        ids = [log.append({"n": i}) for i in range(5)]
        log.flush()
        log.append({"n": 99})
        log.flush()
        for i in ids:
            self.assertTrue(log.verify_receipt(log.get_receipt(i)))
        self.assertTrue(log.verify_chain_integrity()[0])

    def test_tamper_detected(self):
        log = MerkleAuditLog()
        log.append({"a": 1}); log.flush()
        log.append({"b": 2}); log.flush()
        log._batches[0]["root"] = "0" * 64
        self.assertFalse(log.verify_chain_integrity()[0])


class TestRateLimiter(unittest.TestCase):
    def test_capacity(self):
        rl = RateLimiter(window_s=60)
        self.assertEqual([rl.allow("k", 2) for _ in range(3)], [True, True, False])


if __name__ == "__main__":
    unittest.main()
