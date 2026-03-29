"""Accounting Provider Interface — Abstract base class for accounting backends.

Mirrors the EmailProvider pattern from agency/tools/email/interface.py.
Agents call these methods without knowing if the backend is Fatture in Cloud,
Exact Online, or a mock. The as_tools() method wraps everything into
plain functions that the Agent can register.

The tool function names match the existing FIC/Exact tools so that
agent wiring files work identically with mocks or production backends.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass
class Invoice:
    """An invoice from the accounting system."""
    id: str
    invoice_number: str
    customer_name: str
    customer_email: str
    customer_code: str = ""
    amount_net: float = 0.0
    amount_gross: float = 0.0
    amount_paid: float = 0.0
    currency: str = "EUR"
    due_date: date | None = None
    days_overdue: int = 0
    description: str = ""
    status: str = "not_paid"  # not_paid, paid, partial, disputed
    language: str = "en"


@dataclass
class Contact:
    """A customer contact from the accounting system."""
    name: str
    email: str
    phone: str = ""
    job_title: str = ""
    customer_code: str = ""


@dataclass
class CompanyInfo:
    """Company information from the accounting system."""
    name: str
    vat_number: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class PaymentStatus:
    """Payment status for a specific invoice."""
    invoice_number: str
    status: str  # paid, not_paid, partial, overdue, not_found, disputed
    amount_due: float = 0.0
    amount_paid: float = 0.0
    due_date: str | None = None
    customer_name: str = ""


@dataclass
class ProviderState:
    """Snapshot of all provider state for save/restore."""
    invoices: dict[str, Any] = field(default_factory=dict)
    contacts: dict[str, list[Any]] = field(default_factory=dict)
    company_info: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


class AccountingProvider(ABC):
    """Abstract base class for accounting providers.

    Implementations: MockAccountingProvider (testing), and future wrappers
    for FattureInCloud, ExactOnline, etc.
    """

    @abstractmethod
    def get_overdue_invoices(self, min_days_overdue: int = 1, min_amount: float = 0.0) -> list[Invoice]:
        """Fetch all overdue unpaid invoices.

        Args:
            min_days_overdue: Only return invoices overdue by at least this many days.
            min_amount: Only return invoices above this amount.

        Returns:
            List of overdue invoices sorted by days_overdue descending.
        """
        ...

    @abstractmethod
    def check_payment_status(self, invoice_number: str) -> PaymentStatus:
        """Check the payment status of a specific invoice.

        Args:
            invoice_number: The invoice number (e.g., "INV-2024-042").

        Returns:
            PaymentStatus with current state.
        """
        ...

    @abstractmethod
    def get_company_info(self) -> CompanyInfo:
        """Get basic info about the connected company.

        Returns:
            CompanyInfo with name, VAT, email, phone.
        """
        ...

    @abstractmethod
    def get_customer_contacts(self, customer_code: str) -> list[Contact]:
        """Get contact details for a customer.

        Args:
            customer_code: Customer identifier in the accounting system.

        Returns:
            List of contacts for this customer.
        """
        ...

    # ----- Convenience: wrap provider methods as agent tools -----

    def as_tools(self) -> list:
        """Return bound methods as a list of tool functions for the agent.

        The function names match the existing FIC/Exact tools so that
        agent wiring files work identically with mocks or production backends.

        Usage:
            accounting = MockAccountingProvider(...)
            agent = Agent(tools=[*accounting.as_tools(), *email.as_tools(), ...])
        """

        def get_overdue_invoices(min_days_overdue: int = 1) -> list:
            """Fetch all overdue unpaid invoices from the accounting system.

            Returns a list of overdue invoices with: invoice_number, customer_name,
            customer_email, amount, currency, due_date, days_overdue, language.
            """
            results = self.get_overdue_invoices(min_days_overdue=min_days_overdue)
            return [
                {
                    "invoice_number": inv.invoice_number,
                    "customer_name": inv.customer_name,
                    "customer_email": inv.customer_email,
                    "customer_code": inv.customer_code,
                    "amount": inv.amount_gross,
                    "amount_net": inv.amount_net,
                    "currency": inv.currency,
                    "due_date": str(inv.due_date) if inv.due_date else "",
                    "days_overdue": inv.days_overdue,
                    "description": inv.description,
                    "language": inv.language,
                }
                for inv in results
            ]

        def check_payment_status(invoice_number: str) -> dict:
            """Check if a specific invoice has been paid.

            Returns the current status: 'overdue', 'paid', 'partial', or 'disputed'.
            """
            ps = self.check_payment_status(invoice_number)
            return {
                "invoice_number": ps.invoice_number,
                "status": ps.status,
                "amount_due": ps.amount_due,
                "amount_paid": ps.amount_paid,
                "due_date": ps.due_date,
                "customer_name": ps.customer_name,
            }

        def get_company_info() -> dict:
            """Get basic info about the connected company."""
            ci = self.get_company_info()
            return {
                "name": ci.name,
                "vat_number": ci.vat_number,
                "email": ci.email,
                "phone": ci.phone,
            }

        def get_customer_contacts(customer_code: str) -> list:
            """Get contact details for a customer by their customer code."""
            contacts = self.get_customer_contacts(customer_code)
            return [
                {
                    "name": c.name,
                    "email": c.email,
                    "phone": c.phone,
                    "job_title": c.job_title,
                }
                for c in contacts
            ]

        return [get_overdue_invoices, check_payment_status, get_company_info, get_customer_contacts]
