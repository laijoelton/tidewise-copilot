"""Tidewise test cases: forecast, document check, financing, guard, policy,
sandbox, agent loop, audit and the red-team suite."""

import unittest
from datetime import date

from tidewise import analysis, cashflow, financing, guard, redteam, trade_docs
from tidewise.agent import Workspace
from tidewise.data import build_demo_company
from tidewise.policy import Action, Policy


class TestTradeDocs(unittest.TestCase):
    def test_bl_weight_discrepancy_found_with_line_evidence(self):
        r = trade_docs.check_shipment(build_demo_company(), "SHP-311")
        self.assertEqual(r["status"], "MISMATCH")
        d = r["discrepancies"][0]
        self.assertEqual(d["field"], "gross_weight_kg")
        self.assertEqual((d["si_value"], d["bl_value"]), (18540.0, 18450.0))
        self.assertEqual(d["si_evidence"]["line"], 9)
        self.assertTrue(r["blocks_lc_payment"])

    def test_clean_shipment_passes(self):
        r = trade_docs.check_shipment(build_demo_company(), "SHP-309")
        self.assertEqual(r["status"], "OK")
        self.assertFalse(r["blocks_lc_payment"])

    def test_amendment_draft_cites_both_values(self):
        draft = trade_docs.draft_amendment_request(build_demo_company(), "SHP-311")
        self.assertIn("18450", draft["body"].replace(",", ""))
        self.assertIn("18540", draft["body"].replace(",", ""))


class TestForecast(unittest.TestCase):
    def setUp(self):
        self.c = build_demo_company()
        self.fc = cashflow.forecast(self.c)

    def test_shortfall_detected_on_timber_bill_date(self):
        self.assertEqual(self.fc["first_buffer_breach"], "2026-10-26")
        self.assertLess(self.fc["trough"]["balance"], 0)
        self.assertGreater(self.fc["funding_gap"], 90_000)

    def test_lc_receivable_delayed_by_discrepancy(self):
        ev = next(e for e in self.fc["events"] if e["source"] == "INV-2041")
        self.assertIn("LC refusal risk", ev["basis"])
        self.assertGreaterEqual(date.fromisoformat(ev["date"]), date(2026, 11, 10))

    def test_doubtful_receivable_excluded(self):
        self.assertIn("INV-2032", [e["source"] for e in self.fc["excluded"]])

    def test_fixing_the_document_removes_the_gap(self):
        ws = Workspace(use_llm=False)
        ws.sandbox.apply_bl_amendment("SHP-311", "t")
        self.assertEqual(trade_docs.check_shipment(ws.company, "SHP-311")["status"], "OK")
        self.assertEqual(cashflow.forecast(ws.company)["funding_gap"], 0)

    def test_alert_codes(self):
        codes = [a["code"] for a in self.fc["alerts"]]
        for code in ("CASH_SHORTFALL", "LC_DISCREPANCY", "DOUBTFUL_RECEIVABLE", "OVERDUE_RECEIVABLE"):
            self.assertIn(code, codes)

    def test_weekly_buckets_reconcile_to_daily(self):
        self.assertEqual(len(self.fc["weekly"]), 13)
        self.assertEqual(self.fc["weekly"][-1]["closing"], self.fc["daily"][-1]["balance"])


class TestAnalysis(unittest.TestCase):
    def test_metrics_present_and_sane(self):
        m = analysis.analyse(build_demo_company())["metrics"]
        self.assertGreater(m["revenue_90d"], 0)
        self.assertTrue(0 < m["gross_margin"] < 1)
        self.assertGreater(m["dso_days"], 0)
        self.assertGreater(m["current_ratio"], 0)


class TestFinancing(unittest.TestCase):
    def setUp(self):
        self.c = build_demo_company()
        self.m = financing.match_for_forecast(self.c, cashflow.forecast(self.c))
        self.by_id = {o["id"]: o for o in self.m["options"]}

    def test_lc_discounting_blocked_by_discrepancy(self):
        lc = self.by_id["LCDISC"]
        self.assertFalse(lc["eligible"])
        self.assertTrue(any("discrepancy" in c["detail"] for c in lc["checks"] if not c["passed"]))

    def test_slow_products_not_on_time(self):
        self.assertFalse(self.by_id["WCLINE"]["on_time"])
        self.assertFalse(self.by_id["GGSME"]["on_time"])

    def test_merchant_cash_advance_flagged_high_cost_and_excluded(self):
        self.assertTrue(self.by_id["MCA"]["high_cost"])
        self.assertNotIn("MCA", [p["id"] for p in self.m["recommended_plan"]])

    def test_plan_covers_need_with_margin(self):
        self.assertTrue(self.m["plan_covers_need"])
        self.assertGreaterEqual(sum(p["amount"] for p in self.m["recommended_plan"]), self.m["need"])

    def test_overdue_invoice_not_financeable(self):
        self.assertNotIn("INV-2038", self.by_id["INVFIN"]["basis"])


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.scans = {s.message_id: s for s in guard.scan_inbox(build_demo_company())}

    def test_legitimate_mail_verified(self):
        self.assertEqual(self.scans["MSG-01"].trust, "verified")
        self.assertEqual(self.scans["MSG-03"].trust, "verified")

    def test_bec_lookalike_quarantined(self):
        checks = {f.check for f in self.scans["MSG-02"].findings}
        self.assertEqual(self.scans["MSG-02"].trust, "quarantined")
        self.assertTrue({"sender_lookalike", "payment_redirect"} <= checks)

    def test_hidden_prompt_injection_quarantined_and_stripped(self):
        s = self.scans["MSG-04"]
        self.assertEqual(s.trust, "quarantined")
        self.assertIn("prompt_injection", {f.check for f in s.findings})
        self.assertNotIn("ignore your previous", s.masked_body.lower())
        self.assertTrue(s.hidden_text_removed)

    def test_masking(self):
        self.assertEqual(guard.mask("acct 8600-1234-5678"), "acct ****-5678")


class TestPolicy(unittest.TestCase):
    def setUp(self):
        self.c = build_demo_company()
        self.p = Policy(self.c)

    def test_small_due_bill_auto(self):
        self.assertEqual(self.p.evaluate(Action("pay_bill", {"bill_id": "BILL-774"})).outcome, "auto")

    def test_large_bill_needs_approval_with_step_up(self):
        d = self.p.evaluate(Action("pay_bill", {"bill_id": "BILL-771"}))
        self.assertEqual(d.outcome, "needs_approval")
        self.assertTrue(d.step_up)

    def test_early_payment_needs_approval(self):
        self.assertEqual(self.p.evaluate(Action("pay_bill", {"bill_id": "BILL-775"})).outcome, "needs_approval")

    def test_unknown_action_blocked(self):
        self.assertEqual(self.p.evaluate(Action("delete_ledger", {})).outcome, "blocked")

    def test_financing_always_needs_owner(self):
        d = self.p.evaluate(Action("apply_financing", {"product_id": "INVFIN", "amount": 1000}))
        self.assertEqual(d.outcome, "needs_approval")

    def test_reminder_cooldown(self):
        a = Action("send_reminder", {"invoice_id": "INV-2038", "to": "accounts@northwindhome.example"})
        self.assertEqual(self.p.evaluate(a).outcome, "auto")
        self.p.reminder_log["INV-2038"] = self.c.as_of
        self.assertEqual(self.p.evaluate(a).outcome, "blocked")


class TestAgentLoop(unittest.TestCase):
    def test_autopilot_end_to_end(self):
        ws = Workspace(use_llm=False)
        r = ws.agent.chat("What should I do this week?")
        outcomes = [(s["type"], s["decision"]["outcome"]) for s in r["steps"] if s["kind"] == "action"]
        self.assertIn(("send_amendment_request", "auto"), outcomes)
        self.assertIn(("pay_bill", "auto"), outcomes)
        self.assertIn(("request_supplier_extension", "needs_approval"), outcomes)
        self.assertEqual(len(ws.sandbox.outbox), 3)            # 1 amendment + 2 reminders
        self.assertEqual({p["action"]["type"] for p in r["pending_approvals"]},
                         {"request_supplier_extension", "apply_financing"})

        for item in ws.agent.pending():
            ws.agent.approve(item["id"], "owner", confirm_amount=item["amount_myr"])
        self.assertEqual(cashflow.forecast(ws.company)["funding_gap"], 0)
        self.assertTrue(ws.audit.verify()["chain_intact"])

    def test_owner_cannot_pay_new_account_via_chat(self):
        ws = Workspace(use_llm=False)
        r = ws.agent.chat("pay BILL-771 to our new account 8600-1234-5678")
        self.assertEqual(r["steps"][0]["decision"]["outcome"], "blocked")
        self.assertIsNone(ws.company.bill("BILL-771").paid_on)

    def test_every_record_has_valid_inclusion_proof(self):
        ws = Workspace(use_llm=False)
        ws.agent.chat("What should I do this week?")
        for rec in ws.audit.records:
            self.assertTrue(ws.audit.receipt(rec["id"])["verified"])


class TestRedTeam(unittest.TestCase):
    def test_all_attacks_handled(self):
        rep = redteam.run()
        failed = [r for r in rep["results"] if not r["passed"]]
        self.assertEqual(failed, [], failed)


if __name__ == "__main__":
    unittest.main()
