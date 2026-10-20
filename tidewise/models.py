"""Domain model for one small business: accounts, ledger, customer orders
(bookings), delivery notes, receivables, payables and payees. Amounts are
stored in the document's own currency; anything that aggregates converts to
the base currency via FX."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

BASE_CURRENCY = "MYR"
# Sandbox reference rates (MYR per unit). Fixed so forecasts are reproducible.
FX_RATES = {"MYR": 1.0, "USD": 4.42, "EUR": 4.81, "SGD": 3.41, "CNY": 0.62}   # book rates; conversions use live rates (fx.py)


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
    category: str          # revenue | cogs | payroll | rent | utilities | logistics | loan | fx | deposit | other
    counterparty: str
    memo: str = ""


@dataclass
class Line:
    """One line on an order, a delivery note or an invoice."""
    sku: str
    description: str
    qty: int
    unit_price: float = 0.0          # delivery notes carry no price

    @property
    def total(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass
class Order:
    """A customer's order — booked through the portal, by email or by sales.
    Its PO number is what the customer's accounts team matches invoices to."""
    id: str
    customer: str
    po_number: str
    placed: date
    currency: str
    lines: List[Line]
    delivery_date: date
    status: str                      # pending | confirmed | in_production | delivered | invoiced | declined
    deposit_pct: float = 0.3
    deposit_paid: float = 0.0
    channel: str = "sales"           # portal | email | sales
    contact: str = ""
    notes: str = ""
    invoice_id: Optional[str] = None
    assessment: Dict = field(default_factory=dict)   # booking checks: risk, capacity, screening

    @property
    def total(self) -> float:
        return round(sum(l.total for l in self.lines), 2)

    @property
    def deposit_due(self) -> float:
        return round(max(0.0, self.total * self.deposit_pct - self.deposit_paid), 2)


@dataclass
class DeliveryNote:
    id: str
    order_id: str
    delivered_on: date
    lines: List[Line]
    signed_by: str


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
    terms: str = "OPEN_ACCOUNT"
    order_id: Optional[str] = None
    lines: List[Line] = field(default_factory=list)
    paid_on: Optional[date] = None
    financed: bool = False           # sold outright: proceeds go to the financier
    pledged_to: Optional[str] = None  # security for invoice financing: still collected by us
    reminders_sent: int = 0
    correction: Optional[str] = None  # None | "sent" | "accepted" — a corrected invoice after a mismatch
    correction_note: str = ""         # what was corrected, for the owner and the forecast


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
    approved: bool = True            # approved by the owner when it was booked


@dataclass
class Payee:
    id: str
    name: str
    bank: str
    account_number: str
    verified: bool = True            # verified out-of-band when first added
    domain: str = ""                 # registered email domain for this supplier


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
    amount: float                    # MYR, positive = outflow
    day_of_month: int


@dataclass
class Product:
    sku: str
    name: str
    unit_price_usd: float
    units_per_week: int              # production capacity


@dataclass
class Profile:
    legal_name: str
    registration: str
    industry: str
    country: str
    years_trading: float
    annual_revenue_myr: float
    credit_band: str                 # A (best) .. E
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
    orders: List[Order]
    deliveries: List[DeliveryNote]
    catalogue: List[Product]
    cash_buffer_myr: float           # owner's chosen minimum cash floor
    loans_outstanding_myr: float = 0.0
    customer_contacts: Dict[str, str] = field(default_factory=dict)   # customer -> registered AR email
    customer_countries: Dict[str, str] = field(default_factory=dict)
    inbox: List[InboundMessage] = field(default_factory=list)
    repayments: List[Dict] = field(default_factory=list)   # {date, amount_myr, label} owed to lenders

    def account(self, account_id: str) -> Account:
        return next(a for a in self.accounts if a.id == account_id)

    def account_for(self, currency: str) -> Optional[Account]:
        return next((a for a in self.accounts if a.currency == currency), None)

    def payee(self, payee_id: str) -> Optional[Payee]:
        return next((p for p in self.payees if p.id == payee_id), None)

    def invoice(self, invoice_id: str) -> Optional[Invoice]:
        return next((i for i in self.invoices if i.id == invoice_id), None)

    def bill(self, bill_id: str) -> Optional[Bill]:
        return next((b for b in self.bills if b.id == bill_id), None)

    def order(self, order_id: str) -> Optional[Order]:
        return next((o for o in self.orders if o.id == order_id), None)

    def delivery_for(self, order_id: str) -> Optional[DeliveryNote]:
        return next((d for d in self.deliveries if d.order_id == order_id), None)

    def product(self, sku: str) -> Optional[Product]:
        return next((p for p in self.catalogue if p.sku == sku), None)

    def cash_myr(self) -> float:
        return round(sum(to_base(a.balance, a.currency) for a in self.accounts), 2)

    def open_invoices(self) -> List[Invoice]:
        return [i for i in self.invoices if i.paid_on is None]

    def open_bills(self) -> List[Bill]:
        return [b for b in self.bills if b.paid_on is None]

    def known_customer(self, name: str) -> bool:
        return name in self.customer_contacts
