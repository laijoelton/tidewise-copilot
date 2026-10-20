"""Live market data from Malaysia's and China's central-bank sources.

Bank Negara Malaysia Open API (api.bnm.gov.my, free, no key):
  * USD/MYR and CNY/MYR today (latest published session), compared with the
    book rate, so the owner sees what foreign receivables are worth today;
  * the 13-week USD/MYR volatility from ~12 months of BNM daily rates, which
    replaces the hand-set 3% in the Monte Carlo forecast (risk.py);
  * the Overnight Policy Rate (OPR), shown next to the financing options.

China Foreign Exchange Trade System, CFETS (chinamoney.com.cn, authorised by
the People's Bank of China to publish the official RMB central parity):
  * the RMB central parity (人民币中间价) for CNY/MYR and USD/CNY;
  * the Loan Prime Rate (LPR, 贷款市场报价利率) 1Y and 5Y.
  CFETS has no documented public API; these are the JSON files its own
  website loads. They were checked against the live site, but may change.

The books keep their fixed sandbox rates (models.FX_RATES), so the demo stays
reproducible; live data adds context and calibrates the risk model.

Never load-bearing: short timeouts, an on-disk cache, a back-off when a host
is unreachable, and every value labelled with its source ("bnm", "cfets" or
"fallback").

    TIDEWISE_LIVE_DATA = auto (default) | off
    python -m tidewise.market        # fetch now and print what Tidewise sees
"""

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from statistics import pstdev
from typing import Any, Callable, Dict, List, Optional

BNM = "https://api.bnm.gov.my/public"
CFETS = "https://www.chinamoney.com.cn/r/cms/www/chinamoney/data"
BNM_HEADERS = {"Accept": "application/vnd.BNM.API.v1+json", "User-Agent": "Tidewise/1.0 (hackathon prototype)"}
CFETS_HEADERS = {"User-Agent": "Mozilla/5.0 (Tidewise hackathon prototype)", "Referer": "https://www.chinamoney.com.cn/"}
BNM_SESSIONS = ("1700", "1200", "1130", "0900")   # latest first; later sessions publish later in the day
TIMEOUT_S = 4
TTL_HOURS = 6
EMPTY_TTL_S = 1800                    # "no records yet" (e.g. 17:00 rate before 5pm): retry in 30 min
RETRY_AFTER_FAIL_S = 600              # host unreachable: don't hang the UI retrying
CACHE = Path(__file__).resolve().parent.parent / ".cache" / "market.json"
HISTORY_MONTHS = 12
TRADING_DAYS_13W = 65
FALLBACK = {"usd_myr_sd_13w": 0.03}

Fetch = Callable[[str], Any]          # url -> parsed JSON (raise on failure); injectable for tests


def enabled() -> bool:
    return (os.environ.get("TIDEWISE_LIVE_DATA") or "auto").strip().lower() != "off"


# -- transport & cache ---------------------------------------------------------------
def _load_cache() -> Dict[str, Any]:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(c), encoding="utf-8")
    except OSError:
        pass


class NoRecords(Exception):
    """The host answered, but has nothing for this query (BNM's 404)."""


def _http(url: str) -> Any:
    headers = BNM_HEADERS if url.startswith(BNM) else CFETS_HEADERS
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoRecords(url) from e
        raise


def _get(url: str, fetch: Optional[Fetch] = None) -> Optional[Any]:
    cache = _load_cache()
    hit = cache.get(url)
    now = time.time()
    if hit:
        ttl = EMPTY_TTL_S if hit.get("empty") else TTL_HOURS * 3600
        if now - hit["at"] < ttl:
            return hit.get("body")
    if not enabled():
        return hit.get("body") if hit else None
    host = url.split("/")[2]
    down = cache.get("__down__", {})
    if fetch is None and now - down.get(host, 0) < RETRY_AFTER_FAIL_S:
        return hit.get("body") if hit else None
    try:
        body = (fetch or _http)(url)
    except NoRecords:
        cache[url] = {"at": now, "empty": True, "body": None}
        _save_cache(cache)
        return None
    except Exception:
        if fetch is None:
            cache.setdefault("__down__", {})[host] = now
            _save_cache(cache)
        return hit.get("body") if hit else None          # stale beats nothing
    cache[url] = {"at": now, "body": body}
    _save_cache(cache)
    return body


# -- BNM parsing (objects or lists, depending on the query) --------------------------
def _rows(body: Any, code: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flatten a BNM exchange-rate response into [{date, mid}] in MYR per 1 unit."""
    if not isinstance(body, dict):
        return []
    data = body.get("data", body)
    items = data if isinstance(data, list) else [data]
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if code and it.get("currency_code") and it["currency_code"].upper() != code.upper():
            continue
        unit = float(it.get("unit") or 1) or 1.0
        rates = it.get("rate", it)
        for r in (rates if isinstance(rates, list) else [rates]):
            if not isinstance(r, dict):
                continue
            mid = r.get("middle_rate")
            if mid is None and r.get("buying_rate") is not None and r.get("selling_rate") is not None:
                mid = (float(r["buying_rate"]) + float(r["selling_rate"])) / 2
            if mid is None:
                continue
            out.append({"date": str(r.get("date") or "")[:10], "mid": float(mid) / unit})
    return sorted(out, key=lambda x: x["date"])


def _opr(body: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(body, dict):
        return None
    data = body.get("data", body)
    if isinstance(data, list):
        data = data[-1] if data else {}
    lvl = data.get("new_opr_level") if isinstance(data, dict) else None
    if lvl is None:
        return None
    return {"level_pct": float(lvl), "date": str(data.get("date") or "")[:10],
            "change_pct": float(data.get("change_in_opr") or 0)}


def _month(code: str, y: int, m: int, fetch: Optional[Fetch]) -> List[Dict[str, Any]]:
    return _rows(_get(f"{BNM}/exchange-rate/{code}/year/{y}/month/{m}?session=1700&quote=rm", fetch), code)


def spot(code: str, fetch: Optional[Fetch] = None, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Latest BNM rate: today's latest published session, else the most recent
    17:00 close from the monthly history."""
    for s in BNM_SESSIONS:
        rows = _rows(_get(f"{BNM}/exchange-rate/{code}?session={s}&quote=rm", fetch), code)
        if rows:
            return {**rows[-1], "session": s}
    today = today or date.today()
    y, m = today.year, today.month
    for _ in range(2):
        rows = _month(code, y, m, fetch)
        if rows:
            return {**rows[-1], "session": "1700"}
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return None


def history(code: str, months: int = HISTORY_MONTHS, today: Optional[date] = None,
            fetch: Optional[Fetch] = None) -> List[Dict[str, Any]]:
    today = today or date.today()
    out: Dict[str, float] = {}
    y, m = today.year, today.month
    for _ in range(months):
        for r in _month(code, y, m, fetch):
            out[r["date"]] = r["mid"]
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return [{"date": d, "mid": v} for d, v in sorted(out.items())]


def vol_13w(rows: List[Dict[str, Any]]) -> Optional[float]:
    """13-week volatility of the rate: s.d. of daily log returns x sqrt(65)."""
    px = [r["mid"] for r in rows if r["mid"] > 0]
    if len(px) < 60:
        return None
    rets = [math.log(b / a) for a, b in zip(px, px[1:])]
    return round(pstdev(rets) * math.sqrt(TRADING_DAYS_13W), 4)


def opr(fetch: Optional[Fetch] = None) -> Optional[Dict[str, Any]]:
    return _opr(_get(f"{BNM}/opr", fetch))


# -- CFETS (China) -------------------------------------------------------------------
def cfets_parity(fetch: Optional[Fetch] = None) -> Dict[str, Dict[str, Any]]:
    """RMB central parity, e.g. {"USD/CNY": {...}, "CNY/MYR": {...}}."""
    body = _get(f"{CFETS}/fx/ccpr.json", fetch)
    if not isinstance(body, dict):
        return {}
    when = str((body.get("data") or {}).get("lastDate") or "")[:10]
    out = {}
    for r in body.get("records") or []:
        try:
            out[r["vrtEName"]] = {"rate": float(r["price"]), "name_cn": r.get("vrtName"), "date": when}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def lpr(fetch: Optional[Fetch] = None) -> Optional[Dict[str, Any]]:
    """China's Loan Prime Rate (1Y and 5Y), in percent."""
    body = _get(f"{CFETS}/currency/bk-lpr.json", fetch)
    if not isinstance(body, dict):
        return None
    terms = {}
    for r in body.get("records") or []:
        try:
            terms[r["termCode"]] = float(r["shibor"])     # CFETS reuses the "shibor" field name here
        except (KeyError, TypeError, ValueError):
            continue
    if "1Y" not in terms:
        return None
    return {"1y_pct": terms["1Y"], "5y_pct": terms.get("5Y"),
            "date": str((body.get("data") or {}).get("showDateCN") or "")[:10]}


# -- public API ----------------------------------------------------------------------
def snapshot(fetch: Optional[Fetch] = None) -> Dict[str, Any]:
    """Everything Tidewise uses, each value labelled with where it came from."""
    from .models import FX_RATES
    usd, cny = spot("USD", fetch), spot("CNY", fetch)
    hist = history("USD", fetch=fetch) if usd else []
    sd = vol_13w(hist)
    o = opr(fetch)
    par = cfets_parity(fetch)
    l = lpr(fetch)
    cn_myr = par.get("CNY/MYR")
    return {
        "providers": {"bnm": "Bank Negara Malaysia Open API (api.bnm.gov.my)",
                      "cfets": "China Foreign Exchange Trade System, for the People's Bank of China (chinamoney.com.cn)"},
        "live_enabled": enabled(),
        "usd_myr": {"rate": round(usd["mid"], 4), "date": usd["date"], "session": usd["session"],
                    "book_rate": FX_RATES["USD"], "source": "bnm"}
        if usd else {"rate": None, "book_rate": FX_RATES["USD"], "source": "fallback"},
        "cny_myr": {"rate": round(cny["mid"], 4), "date": cny["date"], "source": "bnm"} if cny
        else {"rate": None, "source": "fallback"},
        "usd_myr_sd_13w": {"value": sd, "days": len(hist), "source": "bnm"} if sd is not None
        else {"value": FALLBACK["usd_myr_sd_13w"], "days": 0, "source": "fallback"},
        "opr": {**o, "source": "bnm"} if o else {"level_pct": None, "source": "fallback"},
        "china": {
            "cny_myr_parity": {**cn_myr, "source": "cfets"} if cn_myr else {"rate": None, "source": "fallback"},
            "usd_cny_parity": {**par["USD/CNY"], "source": "cfets"} if "USD/CNY" in par
            else {"rate": None, "source": "fallback"},
            "lpr": {**l, "source": "cfets"} if l else {"1y_pct": None, "source": "fallback"},
        },
        # Malaysia's and China's official yuan-ringgit rates, side by side.
        "cny_myr_gap_pct": round((cny["mid"] / cn_myr["rate"] - 1) * 100, 2) if (cny and cn_myr) else None,
    }


def fx_sd(default: float) -> Dict[str, Any]:
    """The USD/MYR 13-week s.d. the Monte Carlo should use, with its source."""
    try:
        s = snapshot()["usd_myr_sd_13w"]
    except Exception:
        return {"value": default, "source": "fallback", "days": 0}
    return s if s["source"] == "bnm" else {"value": default, "source": "fallback", "days": 0}


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
