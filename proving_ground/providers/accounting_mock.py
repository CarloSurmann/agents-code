"""Mock Accounting Provider — In-memory accounting backend for testing.

Stores invoices, contacts, and company info in memory. Supports:
- Seeding with initial data
- Event injection (add invoice, mark paid, mark disputed)
- State snapshot/restore for replay
- SimulatedClock for time-dependent calculations

Follows the MockProvider pattern from agency/tools/email/mock.py.
"""

from __future__ import annotations

import copy
import logging
from datetime import date

from proving_ground.providers.base import (
    AccountingProvider,
    Invoice,
    Contact,
    CompanyInfo,
    PaymentStatus,
    ProviderState,
)
from proving_ground.scenarios.clock import SimulatedClock

logger = logging.getLogger(__name__)


class MockAccountingProvider(AccountingProvider):
    """In-memory accounting provider for testing."""

    def __init__(self, clock: SimulatedClock | None = None):
        self._clock = clock or SimulatedClock()
        self._invoices: dict[str, Invoice] = {}
        self._contacts: dict[str, list[Contact]] = {}
        self._company_info = CompanyInfo(name="Test Company BV")
        self._event_log: list[dict] = []

    # ----- Seeding (bulk initial state) -----

    def seed(
        self,
        invoices: list[Invoice] | None = None,
        contacts: dict[str, list[Contact]] | None = None,
        company_info: CompanyInfo | None = None,
    ) -> None:
        """Pre-populate with test data. Mirrors MockProvider.seed_inbox()."""
        if invoices:
            for inv in invoices:
                self._invoices[inv.invoice_number] = inv
        if contacts:
            self._contacts.update(contacts)
        if company_info:
            self._company_info = company_info

    # ----- Event injection (mid-run state changes) -----

    def add_invoice(self, invoice: Invoice) -> None:
        """Inject a new invoice mid-run."""
        self._invoices[invoice.invoice_number] = invoice
        self._event_log.append({
            "action": "add_invoice",
            "invoice_number": invoice.invoice_number,
            "clock": str(self._clock.today()),
        })
        logger.info(f"[MockAccounting] Added invoice {invoice.invoice_number}")

    def mark_paid(self, invoice_number: str, amount: float | None = None) -> None:
        """Mark an invoice as paid (full or partial)."""
        inv = self._invoices.get(invoice_number)
        if not inv:
            logger.warning(f"[MockAccounting] Invoice {invoice_number} not found for mark_paid")
            return

        if amount is not None and amount < inv.amount_gross:
            inv.amount_paid = amount
            inv.status = "partial"
        else:
            inv.amount_paid = inv.amount_gross
            inv.status = "paid"

        self._event_log.append({
            "action": "mark_paid",
            "invoice_number": invoice_number,
            "amount": amount,
            "status": inv.status,
            "clock": str(self._clock.today()),
        })
        logger.info(f"[MockAccounting] Marked {invoice_number} as {inv.status}")

    def mark_disputed(self, invoice_number: str) -> None:
        """Mark an invoice as disputed."""
        inv = self._invoices.get(invoice_number)
        if not inv:
            logger.warning(f"[MockAccounting] Invoice {invoice_number} not found for mark_disputed")
            return
        inv.status = "disputed"
        self._event_log.append({
            "action": "mark_disputed",
            "invoice_number": invoice_number,
            "clock": str(self._clock.today()),
        })
        logger.info(f"[MockAccounting] Marked {invoice_number} as disputed")

    def remove_invoice(self, invoice_number: str) -> None:
        """Remove an invoice from the system (simulates deletion)."""
        self._invoices.pop(invoice_number, None)
        self._event_log.append({
            "action": "remove_invoice",
            "invoice_number": invoice_number,
            "clock": str(self._clock.today()),
        })

    # ----- Snapshot/restore for replay -----

    def snapshot(self) -> ProviderState:
        """Deep copy of all internal state."""
        return ProviderState(
            invoices=copy.deepcopy({k: v for k, v in self._invoices.items()}),
            contacts=copy.deepcopy(self._contacts),
            company_info={
                "name": self._company_info.name,
                "vat_number": self._company_info.vat_number,
                "email": self._company_info.email,
                "phone": self._company_info.phone,
            },
        )

    def restore(self, state: ProviderState) -> None:
        """Restore from a snapshot."""
        self._invoices = copy.deepcopy(state.invoices)
        self._contacts = copy.deepcopy(state.contacts)
        if state.company_info:
            self._company_info = CompanyInfo(**state.company_info)

    # ----- Test helpers -----

    def get_event_log(self) -> list[dict]:
        """Inspect what mutations happened (for assertions)."""
        return list(self._event_log)

    def get_all_invoices(self) -> dict[str, Invoice]:
        """Direct access to invoice store (for assertions)."""
        return dict(self._invoices)

    # ----- AccountingProvider implementation -----

    def get_overdue_invoices(self, min_days_overdue: int = 1, min_amount: float = 0.0) -> list[Invoice]:
        today = self._clock.today()
        overdue = []

        for inv in self._invoices.values():
            if inv.status in ("paid", "disputed"):
                continue
            if inv.due_date is None:
                continue

            days = (today - inv.due_date).days
            if days < min_days_overdue:
                continue

            amount = inv.amount_gross - inv.amount_paid
            if amount < min_amount:
                continue

            # Update days_overdue dynamically
            inv.days_overdue = days
            overdue.append(inv)

        overdue.sort(key=lambda x: x.days_overdue, reverse=True)
        return overdue

    def check_payment_status(self, invoice_number: str) -> PaymentStatus:
        inv = self._invoices.get(invoice_number)
        if not inv:
            return PaymentStatus(invoice_number=invoice_number, status="not_found")

        amount_due = inv.amount_gross - inv.amount_paid
        status = inv.status
        if status == "not_paid" and inv.due_date and (self._clock.today() - inv.due_date).days > 0:
            status = "overdue"

        return PaymentStatus(
            invoice_number=invoice_number,
            status=status,
            amount_due=round(amount_due, 2),
            amount_paid=round(inv.amount_paid, 2),
            due_date=str(inv.due_date) if inv.due_date else None,
            customer_name=inv.customer_name,
        )

    def get_company_info(self) -> CompanyInfo:
        return self._company_info

    def get_customer_contacts(self, customer_code: str) -> list[Contact]:
        return self._contacts.get(customer_code, [])
