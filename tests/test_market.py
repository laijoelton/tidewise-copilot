"""Live market data (Bank Negara Malaysia + China's CFETS): parsing, sessions,
volatility, caching and fallback. Fixtures copy the shapes of real responses
captured on 20 Oct 2026, so this runs offline."""

import math
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tidewise import market

TODAY = date(2026, 10, 20)

# Shapes as returned by the live services (values trimmed).
BNM_SPOT_0900 = {"data": {"currency_code": "USD", "unit": 1, "rate": {"date": "2026-10-20", "buying_rate": 4.081,
                                                                      "selling_rate": 4.086, "middle_rate": 4.0835}},
                 "meta": {"quote": "rm", "session": "0900", "total_result": 1}}
BNM_OPR = {"data": {"year": 2026, "date": "2026-09-03", "change_in_opr": 0, "new_opr_level": 2.75},
           "meta": {"total_result": 1}}
CFETS_CCPR = {"head": {"rep_code": "200"}, "data": {"lastDate": "2026-10-20 9:15"},
              "records": [{"vrtEName": "USD/CNY", "vrtName": "美元/人民币", "price": "6.7200"},
                          {"vrtEName": "EUR/CNY", "vrtName": "欧元/人民币", "price": "7.5159"},
                          {"vrtEName": "CNY/MYR", "vrtName": "人民币/马来西亚林吉特", "price": "0.60859"}]}
CFETS_LPR = {"head": {"rep_code": "200"}, "data": {"showDateCN": "2026-10-20 9:00"},
             "records": [{"termCode": "1Y", "shibor": "3.00"}, {"termCode": "5Y", "shibor": "3.50"}]}


def fake_world(days=250, vol_daily=0.004, cfets_up=True, bnm_up=True):
    """Fake BNM + CFETS. Only the 09:00 session is published (like a morning),
    and history alternates returns so the volatility is known exactly."""
    start = TODAY - timedelta(days=days)
    hist, px = [], 4.08
    for i in range(days):
        px *= math.exp(vol_daily if i % 2 else -vol_daily)
        hist.append({"date": (start + timedelta(days=i)).isoformat(), "middle_rate": round(px, 6)})
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith(market.BNM):
            if not bnm_up:
                raise OSError("unreachable")
            path = url[len(market.BNM):]
            if path == "/opr":
                return BNM_OPR
            if "/year/" in path:
                code = path.split("/")[2]
                y, m = int(path.split("/")[4]), int(path.split("/")[6].split("?")[0])
                rows = [r for r in hist if r["date"].startswith(f"{y}-{m:02d}")] if code == "USD" else \
                       [{"date": f"{y}-{m:02d}-15", "middle_rate": 0.6086}]
                return {"data": {"currency_code": code, "unit": 1, "rate": rows}}
            if path.startswith("/exchange-rate/USD?session=0900"):
                return BNM_SPOT_0900
            raise market.NoRecords(url)                     # later sessions not published yet
        if url.startswith(market.CFETS):
            if not cfets_up:
                raise OSError("unreachable")
            return CFETS_CCPR if url.endswith("ccpr.json") else CFETS_LPR
        raise OSError("unknown host")
    fetch.calls = calls
    return fetch


class TestMarket(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._cache, self._env = market.CACHE, os.environ.get("TIDEWISE_LIVE_DATA")
        market.CACHE = Path(self.tmp.name) / "market.json"
        os.environ["TIDEWISE_LIVE_DATA"] = "auto"

    def tearDown(self):
        market.CACHE = self._cache
        if self._env is None:
            os.environ.pop("TIDEWISE_LIVE_DATA", None)
        else:
            os.environ["TIDEWISE_LIVE_DATA"] = self._env
        self.tmp.cleanup()

    def test_spot_uses_latest_published_session(self):
        s = market.spot("USD", fake_world(), today=TODAY)
        self.assertAlmostEqual(s["mid"], 4.0835)
        self.assertEqual(s["session"], "0900")                              # 17:00 not out yet

    def test_spot_falls_back_to_monthly_close(self):
        s = market.spot("CNY", fake_world(), today=TODAY)                   # no session data for CNY
        self.assertAlmostEqual(s["mid"], 0.6086)

    def test_no_records_does_not_trip_the_offline_backoff(self):
        f = fake_world()
        market.spot("USD", f, today=TODAY)
        self.assertNotIn("__down__", market._load_cache())

    def test_list_shaped_and_bid_ask_only(self):
        body = {"data": [{"currency_code": "EUR", "unit": 1, "rate": {"date": "2026-10-19", "middle_rate": 5.0}},
                         {"currency_code": "JPY", "unit": 100, "rate": {"date": "2026-10-19", "buying_rate": 2.7,
                                                                        "selling_rate": 2.9}}]}
        rows = market._rows(body, "JPY")
        self.assertAlmostEqual(rows[0]["mid"], 0.028)                       # per 1 yen, not per 100

    def test_volatility_from_history(self):
        hist = market.history("USD", today=TODAY, fetch=fake_world(vol_daily=0.004))
        self.assertGreater(len(hist), 200)
        self.assertAlmostEqual(market.vol_13w(hist), 0.004 * math.sqrt(65), places=3)
        self.assertIsNone(market.vol_13w(hist[:30]))

    def test_opr_and_china_rates(self):
        f = fake_world()
        self.assertEqual(market.opr(f)["level_pct"], 2.75)
        par = market.cfets_parity(f)
        self.assertAlmostEqual(par["CNY/MYR"]["rate"], 0.60859)
        self.assertAlmostEqual(par["USD/CNY"]["rate"], 6.72)
        self.assertEqual(par["CNY/MYR"]["date"], "2026-10-20")
        self.assertEqual(market.lpr(f), {"1y_pct": 3.0, "5y_pct": 3.5, "date": "2026-10-20"})

    def test_snapshot_labels_every_source(self):
        s = market.snapshot(fake_world())
        self.assertEqual(s["usd_myr"]["source"], "bnm")
        self.assertEqual(s["opr"]["source"], "bnm")
        self.assertEqual(s["china"]["cny_myr_parity"]["source"], "cfets")
        self.assertEqual(s["china"]["lpr"]["source"], "cfets")
        self.assertIsNotNone(s["cny_myr_gap_pct"])

    def test_one_side_down_keeps_the_other(self):
        s = market.snapshot(fake_world(cfets_up=False), use_saved=False)
        self.assertEqual(s["usd_myr"]["source"], "bnm")
        self.assertEqual(s["china"]["lpr"]["source"], "fallback")
        self.assertIsNone(s["cny_myr_gap_pct"])

    def test_everything_down_falls_back_and_says_so(self):
        s = market.snapshot(fake_world(cfets_up=False, bnm_up=False), use_saved=False)
        self.assertEqual(s["usd_myr_sd_13w"], {"value": 0.03, "days": 0, "source": "fallback"})
        self.assertEqual(s["opr"]["source"], "fallback")
        self.assertEqual(s["china"]["cny_myr_parity"]["source"], "fallback")

    def test_offline_uses_saved_real_data_and_labels_it(self):
        s = market.snapshot(fake_world(cfets_up=False, bnm_up=False))
        self.assertEqual(s["usd_myr"]["source"], "saved")
        self.assertEqual(s["china"]["lpr"]["source"], "saved")
        self.assertEqual(s["usd_myr_sd_13w"]["source"], "saved")
        self.assertGreater(s["usd_myr_sd_13w"]["days"], 200)
        self.assertTrue(0.005 < s["usd_myr_sd_13w"]["value"] < 0.10)

    def test_saved_snapshot_is_valid(self):
        sv = market._saved()
        self.assertEqual(sv["captured"], "2026-10-20")
        self.assertAlmostEqual(sv["cfets"]["cny_myr"]["rate"], 0.60859)
        self.assertEqual(sv["bnm"]["opr"]["level_pct"], 2.75)

    def test_pages_never_wait_on_the_network(self):
        # Without a fetch function, snapshot() only reads the cache / saved file.
        calls = []
        orig = market._http
        market._http = lambda url: calls.append(url) or (_ for _ in ()).throw(OSError("blocked"))
        try:
            market.snapshot()
            market.fx_sd(0.03)
        finally:
            market._http = orig
        self.assertEqual(calls, [])

    def test_refresh_fills_cache_for_snapshot(self):
        market.refresh(fake_world())
        s = market.snapshot()                                               # cache only
        self.assertEqual(s["usd_myr"]["source"], "bnm")
        self.assertEqual(s["china"]["cny_myr_parity"]["source"], "cfets")

    def test_cache_avoids_refetching(self):
        f = fake_world()
        market.opr(f)
        n = len(f.calls)
        market.opr(f)
        self.assertEqual(len(f.calls), n)

    def test_off_switch_never_calls_network(self):
        os.environ["TIDEWISE_LIVE_DATA"] = "off"
        f = fake_world()
        self.assertIsNone(market.opr(f))
        self.assertEqual(f.calls, [])

    def test_fx_alert_uses_live_rate(self):
        from tidewise import cashflow
        from tidewise.data import build_demo_company
        market.refresh(fake_world())                                        # warm the cache
        text = cashflow._fx_detail(build_demo_company(), 100_000)
        self.assertIn("Bank Negara", text)
        self.assertIn("less", text)                                         # 4.08 < book 4.42


if __name__ == "__main__":
    unittest.main()
