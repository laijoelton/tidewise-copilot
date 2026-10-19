"""Tests for the ML risk models and how they plug into guard, policy and forecast."""

import unittest

from tidewise import cashflow, guard, ml
from tidewise.data import build_demo_company
from tidewise.models import Bill, InboundMessage
from tidewise.policy import Action, Policy


class TestThreatClassifier(unittest.TestCase):
    def test_heldout_quality(self):
        ev = ml.threat_model().evaluate()
        both = ev["detection_on_heldout"]["rules_plus_model"]
        self.assertGreaterEqual(both["threat_recall"], 0.9)
        self.assertLessEqual(both["false_alarm_rate"], 0.05)
        self.assertGreater(both["threat_recall"], ev["detection_on_heldout"]["rules_only"]["threat_recall"])

    def test_demo_inbox_scores(self):
        scans = {s.message_id: s for s in guard.scan_inbox(build_demo_company())}
        self.assertLess(scans["MSG-01"].ml["threat"], ml.REVIEW_AT)
        self.assertEqual(scans["MSG-02"].ml["label"], "payment_fraud")
        self.assertEqual(scans["MSG-04"].ml["label"], "prompt_injection")

    def test_paraphrase_quarantined_by_model(self):
        c = build_demo_company()
        s = guard.scan(c, InboundMessage("P", c.as_of, "ar@vendor.example", "AR", "Remittance",
                                         "Kindly amend the remittance instructions to account 4410-2230-8812 before Friday."))
        self.assertEqual(s.trust, "quarantined")
        self.assertIn("ml_threat", {f.check for f in s.findings})


class TestPaymentAnomaly(unittest.TestCase):
    def test_eval_rates(self):
        ev = ml.PaymentAnomaly.evaluate()
        self.assertGreaterEqual(ev["detection_rate"], 0.9)
        self.assertLessEqual(ev["false_alarm_rate"], 0.05)

    def test_normal_bill_stays_auto(self):
        self.assertEqual(Policy(build_demo_company()).evaluate(Action("pay_bill", {"bill_id": "BILL-774"})).outcome, "auto")

    def test_padded_bill_escalated(self):
        c = build_demo_company()
        c.bills.append(Bill("BILL-799", "PAY-KEMAS", "MYR", 9_400, c.as_of, "rush cartons"))
        d = Policy(c).evaluate(Action("pay_bill", {"bill_id": "BILL-799"}))
        self.assertEqual(d.outcome, "needs_approval")
        self.assertFalse(next(x for x in d.checks if x.name == "payment pattern (ML)").passed)

    def test_no_history_payee_escalated(self):
        d = Policy(build_demo_company()).evaluate(Action("pay_bill", {"bill_id": "BILL-772"}))
        self.assertEqual(d.outcome, "needs_approval")


class TestCollectionRisk(unittest.TestCase):
    def test_eval_auc(self):
        self.assertGreaterEqual(ml.collection_model().evaluate()["roc_auc"], 0.8)

    def test_ranks_chronic_late_payer_highest(self):
        c = build_demo_company()
        oak = ml.collection_model().score(c, c.invoice("INV-2032"))
        kiy = ml.collection_model().score(c, c.invoice("INV-2045"))
        self.assertGreater(oak, 0.8)
        self.assertLess(kiy, 0.2)

    def test_risk_attached_to_forecast_receipts(self):
        ev = [e for e in cashflow.forecast(build_demo_company())["events"] if e["source"] == "INV-2038"][0]
        self.assertTrue(0 <= ev["late_risk"] <= 1)


if __name__ == "__main__":
    unittest.main()
