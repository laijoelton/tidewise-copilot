"""Impact panel: every figure comes from the session's own records."""

import unittest

from tidewise import financing, cashflow, impact
from tidewise.agent import Workspace


def run_demo(ws):
    ws.agent.chat("What should I do this week?")
    for a in list(ws.agent.pending()):
        ws.agent.approve(a["id"], "owner", confirm_amount=a["amount_myr"])
    ws.agent.chat("make me 95% safe")
    for a in list(ws.agent.pending()):
        ws.agent.approve(a["id"], "owner", confirm_amount=a["amount_myr"])


class TestImpact(unittest.TestCase):
    def test_before_anything_is_done(self):
        r = impact.impact(Workspace(use_llm=False))
        self.assertFalse(r["acted"])
        self.assertEqual(r["cash"]["gap_closed"], 0)
        self.assertEqual(r["cost"]["spent"], 0)
        self.assertIsNone(r["cost"]["saved_vs_fast_cash"])
        self.assertEqual(r["work"]["actions"], 0)

    def test_fast_cash_priced_like_the_financing_page(self):
        ws = Workspace(use_llm=False)
        m = financing.match_for_forecast(ws.company, cashflow.forecast(ws.company))
        mca = next(o for o in m["options"] if o["id"] == "MCA")
        fast = impact.impact(ws)["cost"]["fast_cash"]
        self.assertEqual(fast["cost"], mca["cost"])
        self.assertEqual(fast["apr"], mca["effective_apr"])

    def test_after_the_demo(self):
        ws = Workspace(use_llm=False)
        run_demo(ws)
        r = impact.impact(ws)
        self.assertTrue(r["acted"])
        self.assertEqual(r["cash"]["gap_now"], 0)
        self.assertEqual(r["cash"]["gap_closed"], r["cash"]["gap_before"])
        self.assertLess(r["cash"]["risk_now"], 0.05)
        # Cost = what was actually charged: the supplier surcharge + financing fees.
        self.assertAlmostEqual(r["cost"]["spent"], r["cost"]["surcharges"] + r["cost"]["financing"], places=2)
        self.assertGreater(r["cost"]["saved_vs_fast_cash"], 0)
        # Fraud: the two quarantined scam emails, by the amounts they asked for.
        self.assertEqual(r["fraud"]["blocked_myr"], 146_000)
        self.assertEqual(len(r["fraud"]["risky_orders_declined"]), 1)
        self.assertGreater(r["work"]["actions"], 5)
        self.assertEqual(r["receivables_sooner"][0]["invoice"], "INV-2041")


if __name__ == "__main__":
    unittest.main()
