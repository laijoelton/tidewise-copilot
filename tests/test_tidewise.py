"""Tidewise test cases: three-way match, bookings, forecast, financing,
guard, policy, sandbox, agent loop, audit and the red-team suite."""

import unittest
from datetime import date

from tidewise import analysis, bookings, cashflow, financing, guard, matching, redteam
from tidewise.agent import Workspace
from tidewise.data import build_demo_company
from tidewise.policy import Action, Policy


class TestThreeWayMatch(unittest.TestCase):
    def test_price_typo_found_against_po(self):
        r = matching.three_way_match(build_demo_company(), "INV-2041")
        self.assertEqual(r["status"], "MISMATCH")
        d = r["discrepancies"][0]
        self.assertEqual((d["field"], d["po"], d["invoice"]), ("unit_price", 359, 395))
        self.assertEqual(d["impact"], 1440)
        self.assertEqual((r["po_total"], r["invoice_total"]), (37160, 38600))
        self.assertTrue(r["blocks_payment"])

    def test_clean_invoice_matches(self):
        r = matching.three_way_match(build_demo_company(), "INV-2044")
        self.assertEqual(r["status"], "MATCHED")
        self.assertFalse(r["blocks_payment"])

    def test_billing_more_than_delivered_is_caught(self):
        c = build_demo_company()
        c.invoice("INV-2044").lines[0].qty = 70          # delivered 60
        kinds = {d["field"] for d in matching.three_way_match(c, "INV-2044")["discrepancies"]}
        self.assertTrue({"quantity", "delivered"} <= kinds)

    def test_correction_draft_and_hold_states(self):
        ws = Workspace(use_llm=False)
        draft = matching.draft_correction(ws.company, "INV-2041")
        self.assertEqual(draft["new_total"], 37160)
        self.assertIn("359.00", draft["body"])
        ws.sandbox.issue_corrected_invoice("INV-2041", draft["to"], draft["subject"], draft["body"], "k")
        m = matching.three_way_match(ws.company, "INV-2041")
        self.assertEqual((m["status"], m["expected_delay_days"]), ("AWAITING_APPROVAL", matching.HOLD_DAYS_CORRECTION_SENT))
        ws.sandbox.customer_accepts_correction("INV-2041", "k2")
        self.assertEqual(matching.three_way_match(ws.company, "INV-2041")["status"], "MATCHED")


class TestBookings(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(use_llm=False)
        self.c = self.ws.company

    def test_known_low_risk_customer_can_auto_confirm(self):
        ax = self.c.order("ORD-322").assessment
        self.assertEqual(ax["verdict"], "confirm")
        self.assertEqual(ax["deposit_pct"], 0.3)

    def test_new_customer_goes_to_owner_with_higher_deposit(self):
        ax = self.c.order("ORD-323").assessment
        self.assertEqual(ax["verdict"], "owner")
        self.assertEqual(ax["deposit_pct"], 0.5)

    def test_injected_booking_declined(self):
        ax = self.c.order("ORD-324").assessment
        self.assertEqual((ax["verdict"], ax["screening"]["trust"]), ("decline", "quarantined"))

    def test_known_name_from_unknown_address_is_not_trusted(self):
        o = bookings.new_booking(self.c, "Harbour & Hearth Pty Ltd", "purchasing@hh-group.example", "PO-X",
                                 [("RT-STOOL", 20)], self.c.as_of.replace(month=12))
        self.assertFalse(o.assessment["contact_matches"])
        self.assertEqual(o.assessment["verdict"], "owner")

    def test_prices_come_from_catalogue(self):
        o = bookings.new_booking(self.c, "Kiyomi Living KK", "keiri@kiyomi.example", "PO-K-200",
                                 [("RL-CHAIR", 10)], self.c.as_of.replace(month=12))
        self.assertEqual(o.lines[0].unit_price, 95)

    def test_unknown_sku_rejected(self):
        with self.assertRaises(ValueError):
            bookings.new_booking(self.c, "Kiyomi Living KK", "keiri@kiyomi.example", "PO", [("XX-FAKE", 1)],
                                 self.c.as_of.replace(month=12))


class TestForecast(unittest.TestCase):
    def setUp(self):
        self.c = build_demo_company()
        self.fc = cashflow.forecast(self.c)

    def test_shortfall_detected_on_timber_bill_date(self):
        self.assertEqual(self.fc["first_buffer_breach"], "2026-10-26")
        self.assertLess(self.fc["trough"]["balance"], 0)
        self.assertGreater(self.fc["funding_gap"], 80_000)

    def test_mismatched_invoice_delayed_by_hold(self):
        ev = next(e for e in self.fc["events"] if e["source"] == "INV-2041")
        self.assertIn("on hold", ev["basis"])
        self.assertGreaterEqual(date.fromisoformat(ev["date"]), date(2026, 11, 10))

    def test_order_book_deposit_in_forecast(self):
        self.assertTrue(any(e["source"] == "ORD-320" and "deposit" in e["label"] for e in self.fc["events"]))

    def test_doubtful_receivable_excluded(self):
        self.assertIn("INV-2032", [e["source"] for e in self.fc["excluded"]])

    def test_accepted_correction_removes_the_gap(self):
        ws = Workspace(use_llm=False)
        d = matching.draft_correction(ws.company, "INV-2041")
        ws.sandbox.issue_corrected_invoice("INV-2041", d["to"], d["subject"], d["body"], "a")
        ws.sandbox.customer_accepts_correction("INV-2041", "b")
        self.assertEqual(cashflow.forecast(ws.company)["funding_gap"], 0)

    def test_alert_codes(self):
        codes = [a["code"] for a in self.fc["alerts"]]
        for code in ("CASH_SHORTFALL", "INVOICE_MISMATCH", "DOUBTFUL_RECEIVABLE", "OVERDUE_RECEIVABLE",
                     "DEPOSIT_DUE", "BOOKINGS_PENDING"):
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

    def test_mismatched_invoice_not_financeable(self):
        inv = self.by_id["INVFIN"]
        self.assertTrue(inv["eligible"])
        self.assertNotIn("INV-2041", inv["basis"])
        self.assertTrue(any("doesn't match" in c["detail"] for c in inv["checks"] if not c["passed"]))

    def test_po_financing_only_on_paid_deposits(self):
        po = self.by_id["POFIN"]
        self.assertEqual(po["basis"], ["ORD-318"])

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
        self.assertIn(("send_corrected_invoice", "auto"), outcomes)
        self.assertIn(("confirm_booking", "auto"), outcomes)           # known, low-risk customer
        self.assertIn(("confirm_booking", "needs_approval"), outcomes)  # new customer
        self.assertIn(("decline_booking", "auto"), outcomes)           # injected booking
        self.assertIn(("pay_bill", "auto"), outcomes)
        self.assertIn(("request_supplier_extension", "needs_approval"), outcomes)
        self.assertEqual(ws.company.order("ORD-324").status, "declined")
        self.assertEqual(ws.company.invoice("INV-2041").correction, "sent")

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
