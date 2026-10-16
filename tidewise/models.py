"""Domain model for one small business: accounts, ledger, receivables,
payables, payees and shipments. Amounts are stored in the document's own
currency; anything that aggregates converts to the base currency via FX."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

BASE_CURRENCY = "MYR"
# Sandbox reference rates (MYR per unit). Fixed so forecasts are reproducible.
FX_RATES = {"MYR": 1.0, "USD": 4.42, "EUR": 4.81, "SGD": 3.41}


def to_base(amount: float, currency: str) -> float:
    return round(amount * FX_RATES[currency], 2)


@dataclass
class Account:
    id: str
    name: str
    currency: str
    balance: float


@dataclass
class Transaction:
    id: str
    on: date
    account_id: str
    amount: float          # signed, account currency; negative = money out
    category: str          # revenue | cogs | payroll | rent | utilities | logistics | loan | fx | other
    counterparty: str
    memo: str = ""


@dataclass
class Invoice:
    """Money the business is owed (accounts receivable)."""
    id: str
    customer: str
    country: str
    currency: str
    amount: float
    issued: date
    due: date
    terms: str                      # "OPEN_ACCOUNT" | "LC"
    shipment_id: Optional[str] = None
    paid_on: Optional[date] = None
    financed: bool = False          # sold outright (e.g. LC discounted): proceeds go to the bank
    pledged_to: Optional[str] = None  # security for invoice financing: still collected by us
    reminders_sent: int = 0


@dataclass
class Bill:
    """Money the business owes (accounts payable)."""
    id: str
    payee_id: str
    currency: str
    amount: float
    due: date
    description: str
    paid_on: Optional[date] = None
    approved: bool = True           # approved by the owner when it was booked


@dataclass
class Payee:
    id: str
    name: str
    bank: str
    account_number: str
    verified: bool = True           # verified out-of-band when first added
    domain: str = ""                # registered email domain for this supplier


@dataclass
class InboundMessage:
    id: str
    received: date
    sender: str
    display_name: str
    subject: str
    body: str


@dataclass
class Recurring:
    name: str
    category: str
    amount: float                   # MYR, positive = outflow
    day_of_month: int


@dataclass
class Shipment:
    id: str
    invoice_id: str
    vessel: str
    etd: date
    si_text: str                    # shipping instruction (what we told the carrier)
    bl_text: str                    # draft bill of lading (what the carrier issued)
    lc_terms: Dict[str, str] = field(default_factory=dict)


@dataclass
class Profile:
    legal_name: str
    registration: str
    industry: str
    country: str
    years_trading: float
    annual_revenue_myr: float
    credit_band: str                # A (best) .. E
    has_collateral: bool
    owner: str


@dataclass
class Company:
    as_of: date
    profile: Profile
    accounts: List[Account]
    transactions: List[Transaction]
    invoices: List[Invoice]
    bills: List[Bill]
    payees: List[Payee]
    recurring: List[Recurring]
    shipments: List[Shipment]
    cash_buffer_myr: float          # owner's chosen minimum cash floor
    loans_outstanding_myr: float = 0.0
    customer_contacts: Dict[str, str] = field(default_factory=dict)   # customer -> registered AR email
    forwarder_contact: str = ""
    inbox: List[InboundMessage] = field(default_factory=list)
    repayments: List[Dict] = field(default_factory=list)   # {date, amount_myr, label} owed to lenders

    def account(self, account_id: str) -> Account:
        return next(a for a in self.accounts if a.id == account_id)

    def payee(self, payee_id: str) -> Optional[Payee]:
        return next((p for p in self.payees if p.id == payee_id), None)

    def invoice(self, invoice_id: str) -> Optional[Invoice]:
        return next((i for i in self.invoices if i.id == invoice_id), None)

    def bill(self, bill_id: str) -> Optional[Bill]:
        return next((b for b in self.bills if b.id == bill_id), None)

    def shipment(self, shipment_id: str) -> Optional[Shipment]:
        return next((s for s in self.shipments if s.id == shipment_id), None)

    def cash_myr(self) -> float:
        return round(sum(to_base(a.balance, a.currency) for a in self.accounts), 2)

    def open_invoices(self) -> List[Invoice]:
        return [i for i in self.invoices if i.paid_on is None]

    def open_bills(self) -> List[Bill]:
        return [b for b in self.bills if b.paid_on is None]
