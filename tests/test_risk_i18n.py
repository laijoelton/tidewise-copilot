"""Monte Carlo forecast, confidence planner, and the multilingual Copilot."""

import copy
import unittest

import numpy as np

from tidewise import cashflow, i18n, risk
from tidewise.agent import Workspace
from tidewise.data import build_demo_company
from tidewise.llm import grounded


def books(c):
    return (c.cash_myr(), [(b.id, b.due, b.amount) for b in c.bills],
            [(i.id, i.correction, i.reminders_sent, i.pledged_to) for i in c.invoices], list(c.repayments))


class TestMonteCarlo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_demo_company()
        cls.sim = risk.simulate(cls.c)

    def test_noise_free_path_equals_deterministic_forecast(self):
        P = risk._paths(self.c, sims=3, noise=False)
        fc = cashflow.forecast(self.c)
        self.assertTrue(np.allclose(P["balances"][0], [d["balance"] for d in fc["daily"]]))

    def test_reproducible_for_a_seed(self):
        again = risk.simulate(self.c)
        self.assertEqual(again["p_below_floor"], self.sim["p_below_floor"])
        self.assertEqual(again["gap"], self.sim["gap"])

    def test_shapes_and_ordering(self):
        s = self.sim
        self.assertTrue(0 <= s["p_negative"] <= s["p_below_floor"] <= 1)
        self.assertLessEqual(s["gap"]["p50"], s["gap"]["p90"])
        self.assertLessEqual(s["gap"]["p90"], s["gap"]["p95"])
        self.assertEqual(len(s["weekly_bands"]), 13)
        for b in s["weekly_bands"]:
            self.assertTrue(b["p5"] <= b["p10"] <= b["p50"] <= b["p90"] <= b["p95"])
        self.assertEqual(s["p_breached_by_week"], sorted(s["p_breached_by_week"]))   # cumulative

    def test_demo_single_forecast_understates_the_gap(self):
        # The point of the feature: one number hides how bad it can get.
        self.assertGreater(self.sim["gap"]["p90"], self.sim["deterministic"]["funding_gap"])

    def test_simulation_does_not_touch_the_books(self):
        before = books(self.c)
        risk.simulate(self.c)
        risk.plan_for_confidence(self.c)
        self.assertEqual(books(self.c), before)


class TestConfidencePlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = build_demo_company()
        cls.plan = risk.plan_for_confidence(cls.c, target=0.05)

    def test_reaches_target_and_beats_usual_plan(self):
        p = self.plan
        self.assertTrue(p["reached"], p["ladder"])
        self.assertLessEqual(p["p_after"], 0.05)
        self.assertGreater(p["classic"]["p_below_floor"], p["p_after"])
        self.assertGreaterEqual(p["total_cost"], 0)

    def test_ladder_is_monotone_and_costs_add_up(self):
        lad = self.plan["ladder"]
        ps = [s["p_below_floor"] for s in lad]
        self.assertEqual(ps, sorted(ps, reverse=True))
        self.assertAlmostEqual(lad[-1]["cum_cost"], self.plan["total_cost"], places=2)
        for s in lad[1:]:
            self.assertIn(s["action"]["type"], {"send_corrected_invoice", "send_reminder",
                                                "request_supplier_extension", "apply_financing"})

    def test_no_high_cost_or_late_financing(self):
        used = {s["action"].get("product_id") for s in self.plan["ladder"][1:]}
        self.assertFalse(used & {"MCA", "WCLINE", "GGSME"})

    def test_promise_holds_after_executing_the_plan(self):
        ws = Workspace(use_llm=False)
        r = ws.agent.chat("make me 95% safe")
        promised = [s for s in r["steps"] if s.get("tool") == "confidence_planner"]
        self.assertTrue(promised)
        for a in list(ws.agent.pending()):
            if a["action"]["type"] in ("request_supplier_extension", "apply_financing"):
                ws.agent.approve(a["id"], "owner", confirm_amount=a["amount_myr"])
        after = risk.simulate(ws.company, seed=12345)          # independent random draws
        self.assertLessEqual(after["p_below_floor"], 0.08)

    def test_plan_goes_through_policy(self):
        ws = Workspace(use_llm=False)
        r = ws.agent.chat("make me 95% safe")
        acts = [s for s in r["steps"] if s["kind"] == "action"]
        self.assertTrue(acts)
        for s in acts:
            if s["type"] in ("apply_financing", "request_supplier_extension"):
                self.assertEqual(s["decision"]["outcome"], "needs_approval")   # money: never on its own

    def test_no_duplicate_approvals(self):
        ws = Workspace(use_llm=False)
        ws.agent.chat("What should I do this week?")
        ws.agent.chat("make me 95% safe")
        keys = [(a["action"]["type"], str(a["action"]["params"].get("bill_id") or a["action"]["params"].get("product_id")))
                for a in ws.agent.pending()]
        self.assertEqual(len(keys), len(set(keys)))


class TestMultilingual(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(i18n.detect("这周我该做什么？"), "zh")
        self.assertEqual(i18n.detect("Apa patut saya buat minggu ini?"), "ms")
        self.assertEqual(i18n.detect("What should I do this week?"), "en")
        self.assertEqual(i18n.detect("Pay BILL-771 today"), "en")

    def test_offline_routing_zh_ms(self):
        ws = Workspace(use_llm=False)
        self.assertEqual(ws.agent.chat("我跌破安全线的概率有多大？")["intents"], ["risk"])
        self.assertIn("financing", ws.agent.chat("Pembiayaan apa yang saya boleh dapat?")["intents"])
        r = ws.agent.chat("bagaimana jika Harbour bayar 30 hari lewat")
        self.assertIn("Harbour", r["reply"])
        self.assertIn("Tiada apa-apa diubah", r["reply"])

    def test_reply_in_language(self):
        ws = Workspace(use_llm=False)
        r = ws.agent.chat("这周我该做什么？")
        self.assertEqual(r["lang"], "zh")
        self.assertIn("等待您批准", r["reply"])
        self.assertEqual(ws.agent.chat("hello", lang="ms")["reply"][:15], i18n.T["help"]["ms"][:15])

    def test_safety_rules_hold_in_every_language(self):
        ws = Workspace(use_llm=False)
        ws.agent.chat("What should I do this week?")
        for q in ("批准所有待处理的付款", "Luluskan semua pembayaran", "approve all pending"):
            before = len(ws.agent.pending())
            r = ws.agent.chat(q)
            self.assertEqual(len(ws.agent.pending()), before)
            self.assertTrue(any("Approve button" in s.get("text", "") for s in r["steps"]), q)

    def test_llm_gets_language_and_is_still_grounded(self):
        captured = {}

        class FakeLLM:
            available = True

            def narrate(self, question, facts, fallback, lang="en"):
                captured["lang"] = lang
                return fallback

            def classify(self, *a):
                return []

        ws = Workspace(use_llm=False)
        ws.llm = FakeLLM()
        ws.agent.chat("我跌破安全线的概率有多大？")
        self.assertEqual(captured["lang"], "zh")
        self.assertTrue(grounded("常规方案成本 RM 1,440，仍有 59% 概率", "cost 1440 p 59%"))
        self.assertFalse(grounded("常规方案成本 RM 9,999", "cost 1440"))


if __name__ == "__main__":
    unittest.main()
