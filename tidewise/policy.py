"""Permission tiers: what the agent may do on its own, what needs the owner,
and what it may never do.

The agent proposes actions; this module decides. The model is never asked
whether an action is safe — decisions are deterministic rules over the
business's own records, so the same action in the same state always gets
the same answer, and the reason is always printable.

    T0 READ       look at books, forecast, analyse            auto
    T1 NOTIFY     templated messages to registered contacts   auto, rate-limited
    T2 EXECUTE    small payments of approved bills to         auto, inside limits
                  verified payees, small FX conversions
    T3 APPROVE    anything larger, any credit commitment,     owner approval
                  any message that commits the business       (step-up above threshold)
    T4 PROHIBITED new payees, changed bank details, transfers blocked for the agent;
                  to unknown accounts, changing these limits  owner uses their bank

Provenance (taint): every action records where its parameters came from —
the owner's own words, the business's books, or external content (emails,
documents). External provenance can never be auto-executed above T1, and
anything derived from quarantined content is blocked outright.
"""

from dataclasses import asdict, dataclass, field
from datetime import timedelta
from enum import IntEnum
from typing import Any, Dict, List, Optional

from .models import Company, to_base


class Tier(IntEnum):
    T0_READ = 0
    T1_NOTIFY = 1
    T2_EXECUTE = 2
    T3_APPROVE = 3
    T4_PROHIBITED = 4


TIER_LABEL = {0: "T0 · read", 1: "T1 · notify", 2: "T2 · execute within limits",
              3: "T3 · owner approval", 4: "T4 · prohibited"}

# Base tier per action type, before any check can escalate it.
BASE_TIER = {
    "read": Tier.T0_READ,
    "send_reminder": Tier.T1_NOTIFY,
    "send_amendment_request": Tier.T1_NOTIFY,
    "pay_bill": Tier.T2_EXECUTE,
    "fx_convert": Tier.T2_EXECUTE,
    "request_supplier_extension": Tier.T3_APPROVE,
    "apply_financing": Tier.T3_APPROVE,
    "add_payee": Tier.T4_PROHIBITED,
    "change_payee_bank": Tier.T4_PROHIBITED,
    "transfer_external": Tier.T4_PROHIBITED,
    "change_limits": Tier.T4_PROHIBITED,
}


@dataclass
class Limits:
    """Set by the owner. The agent can read these, never write them."""
    auto_pay_per_tx_myr: float = 10_000
    auto_pay_daily_myr: float = 25_000
    auto_fx_usd: float = 5_000
    step_up_above_myr: float = 50_000      # approvals above this must re-type the amount
    reminder_cooldown_days: int = 7
    max_auto_actions_per_turn: int = 10
    approval_ttl_hours: int = 48


@dataclass
class Action:
    type: str
    params: Dict[str, Any]
    provenance: str = "books"            # owner | books | external
    source_ref: Optional[str] = None     # e.g. message id the params came from
    rationale: str = ""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class Decision:
    tier: int
    tier_label: str
    outcome: str                          # auto | needs_approval | blocked
    checks: List[Check] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    step_up: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Policy:
    def __init__(self, company: Company, limits: Optional[Limits] = None):
        self.company = company
        self.limits = limits or Limits()
        self.auto_paid_today = 0.0
        self.reminder_log: Dict[str, Any] = {}       # invoice id -> date last reminded
        self.quarantined_sources: set = set()
        self.auto_actions_this_turn = 0

    # -- the one public entry point ------------------------------------------------
    def evaluate(self, action: Action) -> Decision:
        if action.type not in BASE_TIER:
            return self._decide(Tier.T4_PROHIBITED, [Check("known action", False, f"'{action.type}' is not an allowed action")])
        tier = BASE_TIER[action.type]
        checks: List[Check] = []

        handler = getattr(self, f"_check_{action.type}", None)
        if handler:
            tier = max(tier, handler(action, checks))

        # Provenance / taint rules apply to every action type.
        if action.source_ref and action.source_ref in self.quarantined_sources:
            checks.append(Check("provenance", False, f"parameters come from quarantined content {action.source_ref}"))
            tier = Tier.T4_PROHIBITED
        elif action.provenance == "external" and tier >= Tier.T2_EXECUTE:
            checks.append(Check("provenance", True, "parameters come from external content — owner must confirm"))
            tier = max(tier, Tier.T3_APPROVE)
        else:
            checks.append(Check("provenance", True, f"parameters come from {action.provenance}"))

        if tier <= Tier.T2_EXECUTE and action.type != "read":
            budget_ok = self.auto_actions_this_turn < self.limits.max_auto_actions_per_turn
            checks.append(Check("autonomy budget", budget_ok,
                                f"{self.auto_actions_this_turn}/{self.limits.max_auto_actions_per_turn} autonomous actions this turn"))
            if not budget_ok:
                tier = max(tier, Tier.T3_APPROVE)

        return self._decide(tier, checks)

    def _decide(self, tier: Tier, checks: List[Check]) -> Decision:
        hard_fail = [c for c in checks if not c.passed and c.name in HARD_CHECKS]
        if tier == Tier.T4_PROHIBITED or hard_fail:
            outcome = "blocked"
        elif tier == Tier.T3_APPROVE:
            outcome = "needs_approval"
        else:
            outcome = "auto"
        visible = [c for c in checks if not c.name.startswith("_")]
        reasons = [c.detail for c in visible if not c.passed] or [c.detail for c in visible[:1]]
        amount = next((c for c in checks if c.name == "_amount"), None)
        step_up = outcome == "needs_approval" and amount is not None and float(amount.detail) > self.limits.step_up_above_myr
        return Decision(int(tier), TIER_LABEL[int(tier)], outcome, visible, reasons, step_up)

    # -- per-action checks: append Check rows, return the tier they require -------
    def _check_pay_bill(self, a: Action, checks: List[Check]) -> Tier:
        bill = self.company.bill(a.params.get("bill_id", ""))
        if bill is None:
            checks.append(Check("bill exists", False, f"no bill {a.params.get('bill_id')} in the books"))
            return Tier.T4_PROHIBITED
        amount = to_base(bill.amount, bill.currency)
        checks.append(Check("_amount", True, str(amount)))
        checks.append(Check("bill exists", True, f"{bill.id}: {bill.description}"))
        checks.append(Check("not already paid", bill.paid_on is None,
                            "unpaid" if bill.paid_on is None else f"already paid on {bill.paid_on}"))
        checks.append(Check("bill approved", bill.approved, "approved when booked" if bill.approved else "never approved by owner"))
        payee = self.company.payee(bill.payee_id)
        verified = payee is not None and payee.verified
        checks.append(Check("payee verified", verified,
                            f"{payee.name} · {payee.bank} ****{payee.account_number[-4:]}" if payee else "unknown payee"))
        override = a.params.get("account_number")
        if override and payee and override != payee.account_number:
            checks.append(Check("bank details match", False, "requested account differs from the verified one on file"))
            return Tier.T4_PROHIBITED
        if bill.paid_on is not None or not verified:
            return Tier.T4_PROHIBITED
        tier = Tier.T2_EXECUTE
        within_tx = amount <= self.limits.auto_pay_per_tx_myr
        within_day = self.auto_paid_today + amount <= self.limits.auto_pay_daily_myr
        checks.append(Check("per-payment limit", within_tx, f"RM {amount:,.0f} vs auto limit RM {self.limits.auto_pay_per_tx_myr:,.0f}"))
        checks.append(Check("daily limit", within_day,
                            f"RM {self.auto_paid_today + amount:,.0f} of RM {self.limits.auto_pay_daily_myr:,.0f} today"))
        early = (bill.due - self.company.as_of).days > 5
        checks.append(Check("not paying early", not early, f"due {bill.due.isoformat()}"))
        balance_after = self.company.account("MYR-OPS").balance - amount
        checks.append(Check("funds available", balance_after >= 0, f"operating balance after: RM {balance_after:,.0f}"))
        if balance_after < 0:
            return Tier.T4_PROHIBITED
        if not (within_tx and within_day) or early or not bill.approved:
            tier = Tier.T3_APPROVE
        return tier

    def _check_fx_convert(self, a: Action, checks: List[Check]) -> Tier:
        usd = float(a.params.get("usd", 0))
        available = self.company.account("USD-COLL").balance
        checks.append(Check("_amount", True, str(to_base(usd, "USD"))))
        checks.append(Check("funds available", 0 < usd <= available, f"USD {usd:,.0f} of USD {available:,.0f}"))
        if not 0 < usd <= available:
            return Tier.T4_PROHIBITED
        within = usd <= self.limits.auto_fx_usd
        checks.append(Check("FX auto limit", within, f"USD {usd:,.0f} vs auto limit USD {self.limits.auto_fx_usd:,.0f}"))
        return Tier.T2_EXECUTE if within else Tier.T3_APPROVE

    def _check_send_reminder(self, a: Action, checks: List[Check]) -> Tier:
        inv = self.company.invoice(a.params.get("invoice_id", ""))
        if inv is None or inv.paid_on:
            checks.append(Check("invoice open", False, "no such open invoice"))
            return Tier.T4_PROHIBITED
        contact = self.company.customer_contacts.get(inv.customer)
        to = a.params.get("to", contact)
        checks.append(Check("registered recipient", to == contact and contact is not None,
                            f"sending to {to}" + ("" if to == contact else f" — registered contact is {contact}")))
        if to != contact:
            return Tier.T4_PROHIBITED
        last = self.reminder_log.get(inv.id)
        cool = last is None or (self.company.as_of - last) >= timedelta(days=self.limits.reminder_cooldown_days)
        checks.append(Check("reminder cooldown", cool, "first reminder this week" if cool else f"already reminded on {last}"))
        return Tier.T1_NOTIFY if cool else Tier.T4_PROHIBITED

    def _check_send_amendment_request(self, a: Action, checks: List[Check]) -> Tier:
        to = a.params.get("to")
        ok = to == self.company.forwarder_contact
        checks.append(Check("registered recipient", ok, f"sending to {to}"))
        return Tier.T1_NOTIFY if ok else Tier.T4_PROHIBITED

    def _check_request_supplier_extension(self, a: Action, checks: List[Check]) -> Tier:
        bill = self.company.bill(a.params.get("bill_id", ""))
        ok = bill is not None and bill.paid_on is None
        checks.append(Check("bill open", ok, bill.id if bill else "no such bill"))
        if ok:
            checks.append(Check("_amount", True, str(to_base(bill.amount, bill.currency))))
        checks.append(Check("commits the business", True, "changes terms with a supplier — owner decides"))
        return Tier.T3_APPROVE if ok else Tier.T4_PROHIBITED

    def _check_apply_financing(self, a: Action, checks: List[Check]) -> Tier:
        amount = float(a.params.get("amount", 0))
        checks.append(Check("_amount", True, str(amount)))
        checks.append(Check("credit commitment", True, f"RM {amount:,.0f} with {a.params.get('product_id')} — owner decides"))
        return Tier.T3_APPROVE

    def _check_add_payee(self, a: Action, checks: List[Check]) -> Tier:
        checks.append(Check("payee changes", False, "new payees must be added and verified by the owner in their bank"))
        return Tier.T4_PROHIBITED

    _check_change_payee_bank = _check_add_payee

    def _check_transfer_external(self, a: Action, checks: List[Check]) -> Tier:
        checks.append(Check("transfer target", False, "the agent can only pay approved bills to verified payees"))
        return Tier.T4_PROHIBITED

    def _check_change_limits(self, a: Action, checks: List[Check]) -> Tier:
        checks.append(Check("limits", False, "limits are owner-only configuration"))
        return Tier.T4_PROHIBITED


HARD_CHECKS = {"not already paid", "payee verified", "bank details match", "funds available",
               "registered recipient", "invoice open", "bill exists", "bill open", "known action"}
