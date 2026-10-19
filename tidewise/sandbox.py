"""Sandbox bank: the only code that moves money or sends messages.

It is a simulated core-banking and messaging API over the Company books.
Every operation:
  * requires an idempotency key — replaying the same key returns the original
    receipt instead of paying twice;
  * re-validates its own preconditions (balance, already-paid) even though the
    policy already did, so a bug upstream cannot overdraw or double-pay;
  * returns a receipt with a sandbox reference (SBX-…) and appends one line
    to the execution log (the competition's "sandbox operation log").
"""

import hashlib
import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .financing import CATALOGUE, evaluate
from .guard import mask
from .models import Company, Transaction, to_base


class SandboxError(Exception):
    pass


class SandboxBank:
    def __init__(self, company: Company, log_path: Optional[Path] = None):
        self.company = company
        self.log_path = log_path
        self.log: List[Dict[str, Any]] = []
        self.outbox: List[Dict[str, Any]] = []
        self.applications: List[Dict[str, Any]] = []
        self._receipts: Dict[str, Dict[str, Any]] = {}
        self._tx_seq = 90000

    # -- plumbing ------------------------------------------------------------------
    def _ref(self, key: str) -> str:
        return "SBX-" + hashlib.sha256(key.encode()).hexdigest()[:10].upper()

    def _done(self, key: str, op: str, detail: Dict[str, Any]) -> Dict[str, Any]:
        receipt = {"ref": self._ref(key), "op": op, "idempotency_key": key, "status": "executed",
                   "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sandbox": True, **detail}
        self._receipts[key] = receipt
        self.log.append(receipt)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(receipt, default=str) + "\n")
        return receipt

    def _replay(self, key: str) -> Optional[Dict[str, Any]]:
        if key in self._receipts:
            return {**self._receipts[key], "status": "duplicate_ignored"}
        return None

    def _post(self, account_id: str, amount: float, category: str, counterparty: str, memo: str):
        acct = self.company.account(account_id)
        if acct.balance + amount < 0:
            raise SandboxError(f"insufficient funds in {account_id}")
        acct.balance = round(acct.balance + amount, 2)
        self._tx_seq += 1
        self.company.transactions.append(Transaction(f"TX-{self._tx_seq}", self.company.as_of, account_id,
                                                     round(amount, 2), category, counterparty, memo))

    # -- operations ----------------------------------------------------------------
    def pay_bill(self, bill_id: str, key: str) -> Dict[str, Any]:
        if (r := self._replay(key)):
            return r
        bill = self.company.bill(bill_id)
        if bill is None or bill.paid_on is not None:
            raise SandboxError(f"{bill_id} is not an open bill")
        payee = self.company.payee(bill.payee_id)
        if payee is None or not payee.verified:
            raise SandboxError("payee is not verified")
        amount = to_base(bill.amount, bill.currency)
        self._post("MYR-OPS", -amount, "cogs", payee.name, f"{bill.id} {bill.description}")
        bill.paid_on = self.company.as_of
        return self._done(key, "pay_bill", {"bill_id": bill_id, "amount_myr": amount, "payee": payee.name,
                                            "to_account": f"{payee.bank} ****{payee.account_number[-4:]}"})

    def fx_convert(self, usd: float, key: str) -> Dict[str, Any]:
        if (r := self._replay(key)):
            return r
        myr = to_base(usd, "USD")
        self._post("USD-COLL", -usd, "fx", "Sandbox FX desk", "USD->MYR")
        self._post("MYR-OPS", myr, "fx", "Sandbox FX desk", "USD->MYR")
        return self._done(key, "fx_convert", {"usd": usd, "myr": myr, "rate": round(myr / usd, 4)})

    def send_message(self, kind: str, to: str, subject: str, body: str, key: str, ref: str = "") -> Dict[str, Any]:
        if (r := self._replay(key)):
            return r
        msg = {"kind": kind, "to": to, "subject": subject, "body": mask(body), "about": ref}
        self.outbox.append(msg)
        if kind == "reminder":
            inv = self.company.invoice(ref)
            if inv:
                inv.reminders_sent += 1
        return self._done(key, "send_message", {"kind": kind, "to": to, "subject": subject, "about": ref})

    def submit_financing(self, product_id: str, amount: float, key: str) -> Dict[str, Any]:
        """Simulated lender: re-runs eligibility, then approves and disburses
        instantly (a real lender would take product.days_to_fund)."""
        if (r := self._replay(key)):
            return r
        product = next((p for p in CATALOGUE if p.id == product_id), None)
        if product is None:
            raise SandboxError(f"unknown product {product_id}")
        ev = evaluate(self.company, product, amount, self.company.as_of + timedelta(days=60), 30)
        if not ev["eligible"]:
            raise SandboxError(f"lender declined: {[c['detail'] for c in ev['checks'] if not c['passed']]}")
        draw = round(min(amount, ev["available"]), 2)
        pledged = []
        if product.kind == "invoice":
            # Pledge only as many invoices as the advance needs, latest-due first:
            # financing exists to bring far-off cash forward.
            covered = 0.0
            for inv in sorted((self.company.invoice(i) for i in ev["basis"]), key=lambda i: i.due, reverse=True):
                if covered >= draw:
                    break
                inv.pledged_to = product_id
                pledged.append(inv)
                covered += to_base(inv.amount, inv.currency) * product.advance_rate
            repay_on = max(i.due for i in pledged)
            cost = round(draw * (product.monthly_rate * max(1, (repay_on - self.company.as_of).days) / 30
                                 + product.upfront_fee), 2)
            self.company.repayments.append({"date": repay_on, "amount_myr": round(draw + cost, 2),
                                            "label": f"{product.name} repayment", "source": product_id})
        elif product.kind == "po":
            orders = [self.company.order(o) for o in ev["basis"]]
            for o in orders:
                o.assessment["po_financed"] = product_id
            repay_on = max(o.delivery_date + timedelta(days=30) for o in orders)
            cost = round(draw * (product.monthly_rate * max(1, (repay_on - self.company.as_of).days) / 30
                                 + product.upfront_fee), 2)
            self.company.repayments.append({"date": repay_on, "amount_myr": round(draw + cost, 2),
                                            "label": f"{product.name} repayment", "source": product_id})
        self._post("MYR-OPS", draw, "financing", product.provider, f"{product.name} disbursement")
        app = {"product_id": product_id, "product": product.name, "provider": product.provider,
               "requested": amount, "disbursed": draw, "secured_on": [i.id for i in pledged] or ev["basis"],
               "cost_estimate": ev["cost"], "effective_apr": ev["effective_apr"]}
        self.applications.append(app)
        return self._done(key, "submit_financing", app)

    def extend_supplier_terms(self, bill_id: str, days: int, surcharge: float, key: str) -> Dict[str, Any]:
        """Simulated supplier accepting the extension request."""
        if (r := self._replay(key)):
            return r
        bill = self.company.bill(bill_id)
        if bill is None or bill.paid_on is not None:
            raise SandboxError(f"{bill_id} is not an open bill")
        old_due, old_amount = bill.due, bill.amount
        bill.due = bill.due + timedelta(days=days)
        bill.amount = round(bill.amount * (1 + surcharge), 2)
        return self._done(key, "extend_supplier_terms", {"bill_id": bill_id, "old_due": old_due.isoformat(),
                                                         "new_due": bill.due.isoformat(), "old_amount": old_amount,
                                                         "new_amount": bill.amount})

    # -- invoices & orders ---------------------------------------------------------------
    def issue_corrected_invoice(self, invoice_id: str, to: str, subject: str, body: str, key: str) -> Dict[str, Any]:
        """Re-issues the invoice at the PO's quantities and prices and sends it
        to the customer's registered accounts contact."""
        if (r := self._replay(key)):
            return r
        inv = self.company.invoice(invoice_id)
        order = self.company.order(inv.order_id) if inv and inv.order_id else None
        if inv is None or order is None or inv.paid_on:
            raise SandboxError(f"{invoice_id} is not an open invoice with an order")
        old = inv.amount
        by_sku = {l.sku: l for l in order.lines}
        for line in inv.lines:
            if line.sku in by_sku:
                line.qty, line.unit_price = by_sku[line.sku].qty, by_sku[line.sku].unit_price
        from .matching import three_way_match
        inv.correction_note = "; ".join(d["detail"] for d in three_way_match(self.company, invoice_id)["discrepancies"])
        inv.amount = round(sum(l.total for l in inv.lines), 2)
        inv.correction = "sent"
        self.outbox.append({"kind": "corrected_invoice", "to": to, "subject": subject, "body": mask(body), "about": invoice_id})
        return self._done(key, "issue_corrected_invoice", {"invoice_id": invoice_id, "old_amount": old,
                                                           "new_amount": inv.amount, "to": to})

    def customer_accepts_correction(self, invoice_id: str, key: str) -> Dict[str, Any]:
        """Simulated customer: their accounts team re-runs the match and approves."""
        if (r := self._replay(key)):
            return r
        inv = self.company.invoice(invoice_id)
        if inv is None or inv.correction != "sent":
            raise SandboxError(f"no corrected invoice awaiting approval for {invoice_id}")
        inv.correction = "accepted"
        return self._done(key, "customer_approved_invoice", {"invoice_id": invoice_id})

    def confirm_booking(self, order_id: str, deposit_pct: float, to: str, body: str, key: str) -> Dict[str, Any]:
        if (r := self._replay(key)):
            return r
        o = self.company.order(order_id)
        if o is None or o.status != "pending":
            raise SandboxError(f"{order_id} is not a pending booking")
        o.status, o.deposit_pct = "confirmed", deposit_pct
        if not self.company.known_customer(o.customer):
            self.company.customer_contacts[o.customer] = o.contact
        self.outbox.append({"kind": "booking_confirmation", "to": to, "subject": f"Order {o.id} confirmed — {o.po_number}",
                            "body": mask(body), "about": order_id})
        return self._done(key, "confirm_booking", {"order_id": order_id, "deposit_pct": deposit_pct,
                                                   "deposit_due": o.deposit_due, "currency": o.currency, "to": to})

    def decline_booking(self, order_id: str, reason: str, key: str) -> Dict[str, Any]:
        if (r := self._replay(key)):
            return r
        o = self.company.order(order_id)
        if o is None or o.status != "pending":
            raise SandboxError(f"{order_id} is not a pending booking")
        o.status = "declined"
        o.assessment["declined_because"] = reason
        return self._done(key, "decline_booking", {"order_id": order_id, "reason": reason})

    def record_deposit(self, order_id: str, key: str) -> Dict[str, Any]:
        """Simulated customer paying the deposit on a confirmed order."""
        if (r := self._replay(key)):
            return r
        o = self.company.order(order_id)
        if o is None or o.status not in ("confirmed", "in_production") or o.deposit_due <= 0:
            raise SandboxError(f"no deposit due on {order_id}")
        amount = o.deposit_due
        acct = "USD-COLL" if o.currency == "USD" else "MYR-OPS"
        self._post(acct, amount, "deposit", o.customer, f"deposit {o.id}")
        o.deposit_paid = round(o.deposit_paid + amount, 2)
        return self._done(key, "deposit_received", {"order_id": order_id, "amount": amount, "currency": o.currency})
