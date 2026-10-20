"""Probabilistic cash forecast (Monte Carlo) and the confidence planner.

The 13-week forecast in cashflow.py gives one answer: "the account goes
negative on 26 Oct". Real cash doesn't arrive on one date. This module runs
the same books through thousands of plausible futures and answers the
questions an owner actually has:

  * How likely am I to drop below my floor?        -> p_below_floor
  * How bad could it get?                          -> gap P50 / P90 / P95
  * Which invoice or assumption drives the risk?   -> drivers
  * What's the cheapest set of actions that makes me 95% safe?
                                                   -> plan_for_confidence()

What varies in each simulated future (everything else — bills, payroll,
repayments — is contractual and fixed):

  Open invoices     date = the deterministic expected date
                         + a delay residual drawn from that customer's own
                           payment history (bootstrap) + small jitter
                         + uncertainty in how long a PO-mismatch hold lasts
                    with probability = the ML collection-risk score
                    (ml.CollectionRisk), the invoice instead lands 30+ days
                    past due (the tail the history alone underweights)
                    USD invoices move with a USD/MYR shock
  Doubtful debts    small chance they are recovered after all
  Order deposits    may arrive late or not within the horizon
  Run-rate sales    a per-future demand level and week-to-week noise;
                    materials costs partly track sales

Random draws are keyed per event (common random numbers), so comparing two
plans is a like-for-like comparison, not noise. Fixed seed => reproducible.

The planner never executes anything. It tries real sandbox operations on a
*copy* of the books (corrected invoice, reminder, supplier extension,
invoice / PO financing), measures each one's effect on the probability of a
breach, and returns the cheapest ladder that reaches the target. Turning that
ladder into actions goes through agent.propose() and the permission policy
like everything else.
"""

import copy
import zlib
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from . import cashflow, financing, market, matching
from .i18n import L
from .models import Company, to_base

SIMS = 2000
SEED = 20261012
WEEKS = 13

# Assumptions, kept in one place so they can be shown and challenged.
ASSUMPTIONS = {
    "delay_jitter_days": 2.0,          # on top of the customer's own history
    "default_delay_sd_days": 5.0,      # customers with <2 paid invoices
    "late_tail_extra_days": 12.0,      # mean extra days beyond due+30 when the ML tail hits
    "hold_range": (-0.35, 0.6),        # PO-mismatch hold: -35% .. +60% of the expected days
    "usd_myr_sd": 0.03,                # 13-week USD/MYR move, 1 s.d. — replaced by BNM history when reachable
    "doubtful_recovery_p": 0.25,       # chance a doubtful debt is paid within the horizon
    "deposit_arrives_p": 0.85,         # chance a requested deposit arrives within the horizon
    "deposit_delay_max_days": 14,
    "sales_level_sd": 0.12,            # demand level for the whole quarter
    "sales_week_sd": 0.15,             # week-to-week noise on run-rate sales
    "cost_tracks_sales": 0.5,          # materials move half as much as sales
}


# -- simulation core -----------------------------------------------------------------
def _rng(seed: int, key: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(key.encode())])


def _history(company: Company) -> Dict[str, np.ndarray]:
    out: Dict[str, List[int]] = {}
    for inv in company.invoices:
        if inv.paid_on:
            out.setdefault(inv.customer, []).append((inv.paid_on - inv.due).days)
    return {c: np.array(v, dtype=float) for c, v in out.items()}


def _residuals(hist: Dict[str, np.ndarray], customer: str, rng, n: int) -> np.ndarray:
    h = hist.get(customer)
    if h is None or len(h) < 2:
        return rng.normal(0, ASSUMPTIONS["default_delay_sd_days"], n)
    return rng.choice(h - h.mean(), n) + rng.normal(0, ASSUMPTIONS["delay_jitter_days"], n)


def _paths(company: Company, sims: int = SIMS, seed: int = SEED, weeks: int = WEEKS,
           noise: bool = True) -> Dict[str, Any]:
    """Daily end-of-day balances for every simulated future, plus each
    uncertain event's per-future timing (for driver attribution)."""
    H = weeks * 7
    built = cashflow.build_events(company, H)
    as_of = company.as_of
    hist = _history(company)
    flows = np.zeros((sims, H + 1))          # column H = "after the horizon" (ignored)
    uncertain: List[Dict[str, Any]] = []
    shared = _rng(seed, "__shared__")
    fx_sd = market.fx_sd(ASSUMPTIONS["usd_myr_sd"])["value"]
    fx = 1 + shared.normal(0, fx_sd, sims) if noise else np.ones(sims)
    sales_level = np.clip(1 + shared.normal(0, ASSUMPTIONS["sales_level_sd"], sims), 0.3, None) if noise else np.ones(sims)

    def put(day_idx: np.ndarray, amount: np.ndarray):
        day_idx = np.clip(day_idx, 0, H)
        np.add.at(flows, (np.arange(sims), day_idx), amount)

    def offset(d: date) -> int:
        return (d - as_of).days

    for k, e in enumerate(built["events"]):
        sign = 1.0 if e["direction"] == "in" else -1.0
        base = offset(e["date"])
        amt = np.full(sims, e["amount_myr"] * sign)
        if not noise:
            put(np.full(sims, base), amt)
            continue
        rng = _rng(seed, f"{e['source']}|{e['label']}|{k if e.get('projected') else ''}")
        inv = company.invoice(e["source"])
        order = company.order(e["source"]) if hasattr(company, "order") else None
        days = np.full(sims, base, dtype=float)

        if inv is not None and e["direction"] == "in":
            days += _residuals(hist, inv.customer, rng, sims)
            if "on hold" in e.get("basis", ""):
                h = matching.three_way_match(company, inv.id)["expected_delay_days"]
                lo, hi = ASSUMPTIONS["hold_range"]
                days += rng.triangular(lo * h, 0, hi * h, sims)
            p_late = float(e.get("late_risk") or 0.0)
            tail = rng.random(sims) < p_late
            late_day = offset(inv.due) + 30 + rng.exponential(ASSUMPTIONS["late_tail_extra_days"], sims)
            days = np.where(tail, np.maximum(days, late_day), days)
            if inv.currency == "USD":
                amt = amt * fx
            days = np.maximum(days, 1)
            uncertain.append({"source": inv.id, "label": e["label"], "kind": "receipt",
                              "late": None, "days": days.round().astype(int)})
        elif order is not None and e["direction"] == "in":
            if "deposit" in e["label"]:
                arrives = rng.random(sims) < ASSUMPTIONS["deposit_arrives_p"]
                days += rng.uniform(0, ASSUMPTIONS["deposit_delay_max_days"], sims)
                days = np.where(arrives, days, H)
            else:
                days += _residuals(hist, order.customer, rng, sims)
            if order.currency == "USD":
                amt = amt * fx
            days = np.maximum(days, 1)
            uncertain.append({"source": order.id, "label": e["label"], "kind": "receipt",
                              "days": days.round().astype(int)})
        elif e.get("projected"):
            if e["direction"] == "in":
                amt = amt * np.clip(sales_level * rng.normal(1, ASSUMPTIONS["sales_week_sd"], sims), 0, None)
            else:
                amt = amt * (1 + ASSUMPTIONS["cost_tracks_sales"] * (sales_level - 1))
        put(days.round().astype(int), amt)

    if noise:
        for x in built["excluded"]:
            rng = _rng(seed, f"doubtful|{x['source']}")
            hit = rng.random(sims) < ASSUMPTIONS["doubtful_recovery_p"]
            day = np.where(hit, rng.integers(1, H, sims), H)
            put(day, np.full(sims, x["amount_myr"]))

    opening = company.cash_myr()
    balances = opening + np.cumsum(flows[:, :H], axis=1)
    return {"balances": balances, "uncertain": uncertain, "opening": opening, "H": H, "built": built}


def _summary(company: Company, P: Dict[str, Any], detail: bool = True) -> Dict[str, Any]:
    bal, H = P["balances"], P["H"]
    floor = company.cash_buffer_myr
    mins = bal.min(axis=1)
    below = mins < floor
    gap = np.maximum(0.0, floor - mins)
    out: Dict[str, Any] = {
        "sims": int(bal.shape[0]),
        "p_below_floor": round(float(below.mean()), 4),
        "p_negative": round(float((mins < 0).mean()), 4),
        "gap": {k: round(float(np.percentile(gap, q)), 0) for k, q in (("p50", 50), ("p90", 90), ("p95", 95))},
        "expected_gap": round(float(gap.mean()), 0),
    }
    if not detail:
        return out
    as_of = company.as_of
    first = np.where(below, (bal < floor).argmax(axis=1), -1)
    hits = first[first >= 0]
    out["first_breach"] = None if not len(hits) else {
        "p10": (as_of + timedelta(days=int(np.percentile(hits, 10)))).isoformat(),
        "p50": (as_of + timedelta(days=int(np.percentile(hits, 50)))).isoformat()}
    out["p_breached_by_week"] = [round(float(((first >= 0) & (first <= 7 * w + 6)).mean()), 4) for w in range(H // 7)]
    pct = np.percentile(bal, [5, 10, 50, 90, 95], axis=0)
    out["weekly_bands"] = [{"week": w + 1, "start": (as_of + timedelta(days=7 * w)).isoformat(),
                            **{k: round(float(pct[i, 7 * w + 6]), 0) for i, k in enumerate(("p5", "p10", "p50", "p90", "p95"))},
                            "low_p10": round(float(np.percentile(bal[:, 7 * w:7 * w + 7].min(axis=1), 10)), 0)}
                           for w in range(H // 7)]
    out["daily_bands"] = [{"date": (as_of + timedelta(days=d)).isoformat(),
                           **{k: round(float(pct[i, d]), 0) for i, k in enumerate(("p5", "p10", "p50", "p90", "p95"))}}
                          for d in range(H)]
    # Which uncertain receipts drive the risk: breach rate when it lands late
    # (after its own median date in this simulation) vs when it lands on time.
    drivers = []
    for u in P["uncertain"]:
        d = u["days"]
        if d.std() < 0.5:
            continue
        late = d > np.median(d)
        if late.sum() < 20 or (~late).sum() < 20:
            continue
        lift = below[late].mean() - below[~late].mean()
        drivers.append({"source": u["source"], "label": u["label"],
                        "p_if_late": round(float(below[late].mean()), 3),
                        "p_if_on_time": round(float(below[~late].mean()), 3),
                        "lift": round(float(lift), 3)})
    out["drivers"] = sorted(drivers, key=lambda x: -x["lift"])[:5]
    return out


def simulate(company: Company, sims: int = SIMS, seed: int = SEED, weeks: int = WEEKS) -> Dict[str, Any]:
    """Probabilistic 13-week forecast. Reproducible for a given seed."""
    P = _paths(company, sims, seed, weeks)
    det = cashflow._project(company, P["built"], P["opening"], weeks)
    out = _summary(company, P)
    out.update({"as_of": company.as_of.isoformat(), "cash_buffer": company.cash_buffer_myr,
                "seed": seed, "assumptions": ASSUMPTIONS, "fx_volatility": market.fx_sd(ASSUMPTIONS["usd_myr_sd"]),
                "deterministic": {"funding_gap": det["funding_gap"], "trough": det["trough"],
                                  "first_buffer_breach": det["first_buffer_breach"]}})
    return out


def p_below_floor(company: Company, sims: int = SIMS, seed: int = SEED) -> float:
    return _summary(company, _paths(company, sims, seed), detail=False)["p_below_floor"]


# -- confidence planner ---------------------------------------------------------------
Lever = Dict[str, Any]   # {key, kind, title, cost, apply(company)->None, action: {type, params}}


def _sandbox(c: Company):
    from .sandbox import SandboxBank
    return SandboxBank(c, None)


def _free_levers(c: Company) -> List[Lever]:
    levers: List[Lever] = []
    for inv in c.open_invoices():
        if inv.order_id and inv.lines and matching.three_way_match(c, inv.id)["status"] == "MISMATCH":
            to = c.customer_contacts.get(inv.customer, "")

            def fix(cc, iid=inv.id, to=to):
                _sandbox(cc).issue_corrected_invoice(iid, to, "corrected invoice", "-", f"plan-fix-{iid}")
            levers.append({"key": f"fix:{inv.id}", "kind": "corrected_invoice", "cost": 0.0, "apply": fix,
                           "title": L("plan_fix", inv=inv.id, cust=inv.customer),
                           "action": {"type": "send_corrected_invoice", "invoice_id": inv.id}})
        overdue = (c.as_of - inv.due).days
        if overdue > 0 and not inv.financed:
            def remind(cc, iid=inv.id):
                cc.invoice(iid).reminders_sent += 1
            levers.append({"key": f"remind:{inv.id}", "kind": "reminder", "cost": 0.0, "apply": remind,
                           "title": L("plan_remind", cust=inv.customer, inv=inv.id, days=overdue),
                           "action": {"type": "send_reminder", "invoice_id": inv.id}})
    return levers


def _paid_levers(c: Company) -> List[Tuple[str, Callable[[float], Optional[Lever]], float]]:
    """(key, make(amount) -> lever, max amount). Amount 0 for fixed-size levers."""
    out = []
    cand = financing.supplier_extension_candidate(c)
    if cand:
        bill = cand[0]

        def ext(_amount, bid=bill.id, amt=bill.amount, name=cand[1].name):
            def apply(cc):
                _sandbox(cc).extend_supplier_terms(bid, 30, 0.015, f"plan-ext-{bid}")
            return {"key": f"extend:{bid}", "kind": "supplier_extension", "cost": round(amt * 0.015, 2), "apply": apply,
                    "title": L("plan_ext", name=name, bill=bid, amt=f"RM {amt:,.0f}"),
                    "action": {"type": "request_supplier_extension", "bill_id": bid, "days": 30, "surcharge": 0.015}}
        out.append((f"extend:{bill.id}", ext, 0.0))
    fc = cashflow.forecast(c)
    need_by = date.fromisoformat(fc["first_buffer_breach"]) if fc["first_buffer_breach"] else c.as_of + timedelta(days=30)
    for p in financing.CATALOGUE:
        if p.kind not in ("invoice", "po"):
            continue
        ev = financing.evaluate(c, p, 10_000, need_by, 30)
        if not (ev["eligible"] and ev["on_time"] and not ev["high_cost"]) or ev["available"] < 5_000:
            continue

        def fin(amount, pid=p.id, name=p.name, kind=p.kind):
            def apply(cc):
                _sandbox(cc).submit_financing(pid, amount, f"plan-fin-{pid}-{amount}")
            probe = copy.deepcopy(c)
            before = sum(r["amount_myr"] for r in probe.repayments)
            try:
                apply(probe)
            except Exception:
                return None
            cost = round(sum(r["amount_myr"] for r in probe.repayments) - before - amount, 2)
            return {"key": f"fin:{pid}", "kind": "financing", "cost": cost, "apply": apply, "amount": amount,
                    "title": L("plan_fin", amt=f"RM {amount:,.0f}", name=L("fin_invoice") if kind == "invoice" else L("fin_po")),
                    "action": {"type": "apply_financing", "product_id": pid, "amount": amount}}
        out.append((f"fin:{p.id}", fin, float(ev["available"])))
    return out


def _with(base: Company, levers: List[Lever]) -> Company:
    c = copy.deepcopy(base)
    for lv in levers:
        lv["apply"](c)
    return c


def plan_for_confidence(company: Company, target: float = 0.05, sims: int = 1000, seed: int = SEED,
                        step: float = 5_000) -> Dict[str, Any]:
    """Cheapest set of actions that brings P(below floor) to `target` or less.

    Free actions (corrected invoices, reminders) are kept when they lower the
    breach probability or the expected shortfall. Then every combination of
    fixed-size levers (supplier extension) and every order of the financing
    products is tried; each financing draw is the smallest amount (in `step`
    increments, found by bisection) that reaches the target. The cheapest
    combination that reaches it wins; if none can, the one with the lowest
    probability. Also scores the classic point-estimate plan (cover the
    single forecast's gap) so the owner sees how much risk that plan leaves."""
    from itertools import permutations

    cache: Dict[Tuple, Dict[str, Any]] = {}

    def stats(levers: List[Lever]) -> Dict[str, Any]:
        key = tuple(sorted((lv["key"], lv.get("amount", 0)) for lv in levers))
        if key not in cache:
            c = _with(company, levers)
            cache[key] = _summary(c, _paths(c, sims, seed), detail=False)
        return cache[key]

    def P(levers):
        return stats(levers)["p_below_floor"]

    start = stats([])
    # 1. Free actions.
    free: List[Lever] = []
    for lv in _free_levers(company):
        a, b = stats(free), stats(free + [lv])
        if b["p_below_floor"] < a["p_below_floor"] - 0.002 or b["expected_gap"] < a["expected_gap"] - 100:
            free.append(lv)

    # 2. Search paid combinations.
    paid = _paid_levers(company)
    fixed = [(k, make) for k, make, cap in paid if cap == 0]
    sized = [(k, make, cap) for k, make, cap in paid if cap > 0]

    def fill(base: List[Lever], order) -> List[Lever]:
        chosen = list(base)
        for k, make, cap in order:
            if P(chosen) <= target:
                break
            amounts = [float(a) for a in np.arange(step, cap + 1e-6, step)] or [float(cap)]
            lo, hi, hit = 0, len(amounts) - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                lv = make(amounts[mid])
                if lv is not None and P(chosen + [lv]) <= target:
                    hit, hi = mid, mid - 1
                else:
                    lo = mid + 1
            lv = make(amounts[hit if hit is not None else len(amounts) - 1])
            if lv is not None and P(chosen + [lv]) < P(chosen) - 0.002:
                chosen.append(lv)
        return chosen

    candidates = []
    for mask in range(1 << len(fixed)):
        base = list(free) + [m(0) for i, (_, m) in enumerate(fixed) if mask >> i & 1]
        base = [lv for lv in base if lv is not None]
        for order in (permutations(sized) if sized else [()]):
            chosen = fill(base, order)
            candidates.append((P(chosen), round(sum(lv["cost"] for lv in chosen), 2), chosen))
    reaching = [c for c in candidates if c[0] <= target]
    best_p, best_cost, chosen = min(reaching, key=lambda c: (c[1], c[0])) if reaching else min(candidates, key=lambda c: (c[0], c[1]))

    # 3. Present as a ladder: free first, then the paid steps by risk removed per ringgit.
    ladder = [{"step": 0, "title": L("plan_now"), "cost": 0.0, "cum_cost": 0.0,
               "p_below_floor": start["p_below_floor"], "expected_gap": start["expected_gap"]}]
    done: List[Lever] = []
    todo = list(chosen)
    while todo:
        cur = P(done)
        nxt = min(todo, key=lambda lv: (lv["cost"] > 0, -(cur - P(done + [lv])) / max(lv["cost"], 1.0)))
        todo.remove(nxt)
        done.append(nxt)
        st = stats(done)
        ladder.append({"step": len(ladder), "title": nxt["title"], "kind": nxt["kind"], "cost": nxt["cost"],
                       "cum_cost": round(sum(lv["cost"] for lv in done), 2), "p_below_floor": st["p_below_floor"],
                       "expected_gap": st["expected_gap"], "action": nxt["action"]})

    classic = _classic_plan(company, _free_levers(company))
    cstats = stats(classic["levers"]) if classic["levers"] else start
    return {
        "target": target, "sims": sims, "seed": seed,
        "p_now": start["p_below_floor"], "p_after": best_p, "reached": best_p <= target,
        "total_cost": best_cost, "ladder": ladder, "combinations_tried": len(candidates),
        "classic": {"title": classic["title"], "cost": classic["cost"], "p_below_floor": cstats["p_below_floor"],
                    "expected_gap": cstats["expected_gap"], "gap_p90": cstats["gap"]["p90"]},
        "after": stats(chosen),
        "note": "Simulated on a copy of the books; nothing has been sent, paid or applied for.",
    }


def _classic_plan(company: Company, free: List[Lever]) -> Dict[str, Any]:
    """What a point-estimate tool recommends: the free fixes plus the
    financing matcher's cheapest plan for the single forecast's gap."""
    fixes = [lv for lv in free if lv["kind"] == "corrected_invoice"]
    c = _with(company, fixes)
    fc = cashflow.forecast(c)
    m = financing.match_for_forecast(c, fc)
    levers, cost, names = list(fixes), 0.0, [lv["title"] for lv in fixes]
    if m:
        paid = {key: (make, cap) for key, make, cap in _paid_levers(company)}
        for p in m["recommended_plan"]:
            if p["id"] == "SUPTERMS":
                k = next((k for k in paid if k.startswith("extend:")), None)
                lv = paid[k][0](0) if k else None
            else:
                k = f"fin:{p['id']}"
                lv = paid[k][0](float(p["amount"])) if k in paid else None
            if lv:
                levers.append(lv)
                cost += lv["cost"]
                names.append(lv["title"])
    return {"levers": levers, "cost": round(cost, 2),
            "title": "Cover the single-forecast gap: " + "; ".join(names) if names else "nothing to do"}
