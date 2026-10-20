"""Currency conversion: MYR, USD and CNY at live (or saved) rates, under the same policy."""

import unittest
from unittest import mock

from tidewise import fx, i18n
from tidewise.agent import Workspace
from tidewise.policy import Action

SNAP = {"usd_myr": {"rate": 4.0835, "date": "2026-10-20", "source": "bnm"},
        "cny_myr": {"rate": 0.6109, "date": "2026-10-19", "source": "bnm"},
        "china": {"usd_cny_parity": {"rate": 6.72, "date": "2026-10-20", "source": "cfets"}}}


def snap(*_a, **_k):
    return SNAP


class TestQuote(unittest.TestCase):
    def test_ringgit_legs_use_bank_negara(self):
        q = fx.quote("MYR", "CNY", 10_000, SNAP)
        self.assertAlmostEqual(q["rate"], 1 / 0.6109, places=5)
        self.assertAlmostEqual(q["converted"], 10_000 / 0.6109, places=1)
        self.assertEqual((q["provider"], q["source"], q["myr_value"]), ("Bank Negara Malaysia", "bnm", 10_000))

    def test_usd_cny_uses_cfets_parity(self):
        q = fx.quote("USD", "CNY", 1_000, SNAP)
        self.assertEqual((q["rate"], q["converted"], q["provider"]), (6.72, 6_720, "CFETS"))
        self.assertAlmostEqual(fx.quote("CNY", "USD", 6_720, SNAP)["converted"], 1_000, places=2)

    def test_live_rate_not_book_rate(self):
        self.assertEqual(fx.quote("USD", "MYR", 1_000, SNAP)["converted"], 4_083.5)   # book rate would give 4,420

    def test_missing_rates_fall_back_to_book_and_say_so(self):
        q = fx.quote("USD", "MYR", 1_000, {})
        self.assertEqual((q["converted"], q["source"]), (4_420, "book"))


class TestParse(unittest.TestCase):
    def test_english(self):
        cases = {"convert RM 10,000 to yuan": (10_000, "MYR", "CNY"),
                 "convert USD 3,000 to CNY": (3_000, "USD", "CNY"),
                 "convert 5000 yuan to ringgit": (5_000, "CNY", "MYR"),
                 "exchange $2,500 into rmb": (2_500, "USD", "CNY"),
                 "convert 1000": (1_000, "USD", "MYR")}                   # the old command still works
        for text, (a, f, t) in cases.items():
            self.assertEqual(fx.parse(text), {"amount": a, "from": f, "to": t}, text)

    def test_ringgit_without_target_asks(self):
        self.assertTrue(fx.parse("convert RM 500")["ask"])

    def test_chinese_and_malay(self):
        cases = {("把10000令吉换成人民币", "zh"): (10_000, "MYR", "CNY"),
                 ("兑换5000美元为人民币", "zh"): (5_000, "USD", "CNY"),
                 ("把2万人民币换成令吉", "zh"): (20_000, "CNY", "MYR"),
                 ("tukar RM 10,000 ke yuan", "ms"): (10_000, "MYR", "CNY"),
                 ("tukarkan 3000 dolar as kepada ringgit", "ms"): (3_000, "USD", "MYR")}
        for (text, lang), (a, f, t) in cases.items():
            self.assertEqual(fx.parse(i18n.to_routing_text(text, lang)), {"amount": a, "from": f, "to": t}, text)

    def test_fx_words_leave_other_questions_alone(self):
        self.assertIn("what should i do", i18n.to_routing_text("这周我该做什么", "zh"))
        self.assertIsNone(fx.parse(i18n.to_routing_text("apa patut saya buat minggu ini", "ms")))


@mock.patch("tidewise.market.snapshot", snap)
class TestConversion(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(use_llm=False)
        self.bal = lambda cur: self.ws.company.account_for(cur).balance

    def test_small_conversion_runs_at_the_live_rate(self):
        myr = self.bal("MYR")
        self.ws.agent.chat("convert RM 10,000 to yuan")
        self.assertEqual(self.bal("MYR"), myr - 10_000)
        self.assertAlmostEqual(self.bal("CNY"), round(10_000 / 0.6109, 2), places=2)
        r = self.ws.sandbox.log[-1]
        self.assertEqual((r["op"], r["from"], r["to"], r["rate_source"]), ("fx_convert", "MYR", "CNY", "bnm"))

    def test_chinese_command_executes(self):
        self.ws.agent.chat("把10000令吉换成人民币")
        self.assertGreater(self.bal("CNY"), 16_000)

    def test_large_conversion_waits_for_the_owner(self):
        myr = self.bal("MYR")
        self.ws.agent.chat("convert RM 60,000 to CNY")                    # ≈ USD 14,700 > USD 5,000
        self.assertEqual(self.bal("MYR"), myr)
        a = next(a for a in self.ws.agent.pending() if a["action"]["type"] == "fx_convert")
        self.assertEqual(a["amount_myr"], 60_000)
        self.ws.agent.approve(a["id"], "owner", confirm_amount=a["amount_myr"])
        self.assertEqual(self.bal("MYR"), myr - 60_000)

    def test_cannot_convert_money_it_does_not_have(self):
        d = self.ws.policy.evaluate(Action("fx_convert", {"amount": 5_000, "from": "CNY", "to": "MYR"}, "owner"))
        self.assertEqual(d.outcome, "blocked")

    def test_unknown_currency_blocked(self):
        d = self.ws.policy.evaluate(Action("fx_convert", {"amount": 100, "from": "EUR", "to": "MYR"}, "owner"))
        self.assertEqual(d.outcome, "blocked")

    def test_email_cannot_convert_on_its_own(self):
        d = self.ws.policy.evaluate(Action("fx_convert", {"amount": 100, "from": "USD", "to": "MYR"}, "external",
                                           source_ref="MSG-01"))
        self.assertEqual(d.outcome, "needs_approval")

    def test_old_usd_params_still_work(self):
        d = self.ws.policy.evaluate(Action("fx_convert", {"usd": 1_000}, "owner"))
        self.assertEqual(d.outcome, "auto")

    def test_cash_today_unchanged_by_the_new_account(self):
        self.assertEqual(self.bal("CNY"), 0)


if __name__ == "__main__":
    unittest.main()
