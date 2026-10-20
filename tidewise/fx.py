"""Currency conversion between ringgit, US dollars and Chinese yuan.

  * Rates come from tidewise.market (Bank Negara Malaysia for the ringgit legs,
    CFETS's official central parity for USD/CNY), from the cache or the real
    snapshot saved on 20 Oct 2026, never from the request path.
  * Parsing turns "convert RM 10,000 to yuan" (or the Chinese / Malay forms,
    after i18n.to_routing_text) into {amount, from, to}.
"""

import re
from typing import Any, Dict, Optional, Tuple

from .models import FX_RATES

CURRENCIES = ("MYR", "USD", "CNY")

_WORDS = {
    "us$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD", "$": "USD",
    "rm": "MYR", "myr": "MYR", "ringgit": "MYR",
    "cny": "CNY", "rmb": "CNY", "yuan": "CNY", "renminbi": "CNY", "¥": "CNY",
}
_CUR = r"(us\$|usd|dollars?|ringgit|renminbi|rmb|rm|myr|cny|yuan|¥|\$)"
_FX = re.compile(r"\b(?:convert|exchange)\b\s*(?:" + _CUR + r"\s*)?([\d,]+(?:\.\d+)?)\s*(?:" + _CUR + r"\b)?"
                 r"(?:[^\d]{0,12}?\b(?:to|into|for)\s+" + _CUR + r"(?![a-z]))?")


def parse(text: str) -> Optional[Dict[str, Any]]:
    """{"amount", "from", "to"}; {"ask": ...} if the target can't be inferred; None if not a conversion."""
    m = _FX.search(text.lower())
    if not m:
        return None
    amount = float(m.group(2).replace(",", ""))
    frm = _WORDS.get(m.group(1) or m.group(3) or "")
    to = _WORDS.get(m.group(4) or "")
    if frm is None:
        frm = "USD" if to in (None, "MYR") else "MYR"
    if to is None:
        if frm == "MYR":
            return {"ask": True, "amount": amount, "from": frm}
        to = "MYR"
    if amount <= 0 or frm == to:
        return None
    return {"amount": amount, "from": frm, "to": to}


def normalize(params: Dict[str, Any]) -> Optional[Tuple[float, str, str]]:
    """(amount, from, to) from action params; the old {"usd": x} form means USD -> MYR."""
    if "amount" not in params and "usd" in params:
        params = {"amount": params["usd"], "from": "USD", "to": "MYR"}
    try:
        amount = float(params.get("amount", 0))
    except (TypeError, ValueError):
        return None
    frm, to = str(params.get("from", "")).upper(), str(params.get("to", "")).upper()
    if amount <= 0 or frm not in CURRENCIES or to not in CURRENCIES or frm == to:
        return None
    return amount, frm, to


def quote(frm: str, to: str, amount: float, snap: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Mid-market rate, the amount received, its ringgit and USD value, and where the rate came from."""
    if snap is None:
        from . import market
        snap = market.snapshot()
    per_myr: Dict[str, Tuple[float, str, Optional[str]]] = {"MYR": (1.0, "par", None)}
    for cur, key in (("USD", "usd_myr"), ("CNY", "cny_myr")):
        r = snap.get(key) or {}
        per_myr[cur] = (float(r["rate"]), r.get("source", "saved"), r.get("date")) if r.get("rate") \
            else (FX_RATES[cur], "book", None)
    parity = ((snap.get("china") or {}).get("usd_cny_parity") or {})
    if {frm, to} == {"USD", "CNY"} and parity.get("rate"):
        p = float(parity["rate"])
        rate, source, when = (p if frm == "USD" else 1 / p), parity.get("source", "saved"), parity.get("date")
        provider = "CFETS"
        basis = "official RMB central parity"
    else:
        f, t = per_myr[frm], per_myr[to]
        rate = f[0] / t[0]
        leg = f if frm != "MYR" else t
        source, when = leg[1], leg[2]
        provider = "Bank Negara Malaysia" if source != "book" else "book rate"
        basis = "Bank Negara exchange rate"
    myr_value = round(amount * per_myr[frm][0], 2)
    return {"from": frm, "to": to, "amount": round(amount, 2), "rate": round(rate, 6),
            "converted": round(amount * rate, 2), "myr_value": myr_value,
            "usd_value": round(myr_value / per_myr["USD"][0], 2),
            "source": source, "date": when, "provider": provider, "basis": basis}
