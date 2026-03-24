"""Fatture in Cloud connector — tools for reading invoices and payment status.

This is the accounting connector for Italian SMBs using Fatture in Cloud.
It reads invoices, checks payment status, and identifies overdue receivables.

Setup:
    1. Get an access token from Fatture in Cloud (Settings → Connected Apps)
    2. Set FIC_ACCESS_TOKEN and FIC_COMPANY_ID in your .env file

API docs: https://developers.fattureincloud.it/api-reference
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

import fattureincloud_python_sdk as fic


def _get_client() -> fic.ApiClient:
    """Create an authenticated API client from environment variables."""
    token = os.environ.get("FIC_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "FIC_ACCESS_TOKEN not set. Get a token from Fatture in Cloud: "
            "Settings → Connected Apps → generate a personal token."
        )
    config = fic.Configuration()
    config.access_token = token
    return fic.ApiClient(config)


def _get_company_id() -> int:
    """Get the company ID from environment."""
    cid = os.environ.get("FIC_COMPANY_ID")
    if not cid:
        raise RuntimeError(
            "FIC_COMPANY_ID not set. Find it in Fatture in Cloud under "
            "your company name (Codice Cliente)."
        )
    return int(cid)


# ---------------------------------------------------------------------------
# Tools (these are what the agent calls)
# ---------------------------------------------------------------------------


def get_overdue_invoices(min_days_overdue: int = 1) -> list[dict]:
    """Fetch all overdue unpaid invoices from Fatture in Cloud.

    Returns a list of overdue invoices with: id, number, customer_name,
    customer_email, amount_net, amount_gross, currency, due_date,
    days_overdue, description.

    Args:
        min_days_overdue: Only return invoices overdue by at least this many days.
    """
    client = _get_client()
    company_id = _get_company_id()
    docs_api = fic.IssuedDocumentsApi(client)

    # Fetch all invoices
    result = docs_api.list_issued_documents(
        company_id=company_id,
        type="invoice",
        fields="id,number,numeration,var_date,entity,payments_list,items_list,amount_net,amount_gross",
    )

    if not result.data:
        return []

    today = date.today()
    overdue = []

    for doc in result.data:
        # Check payments_list for unpaid items
        if not doc.payments_list:
            continue

        for payment in doc.payments_list:
            if payment.status == "not_paid" and payment.due_date:
                due = payment.due_date
                if isinstance(due, str):
                    due = date.fromisoformat(due)

                days_overdue = (today - due).days
                if days_overdue >= min_days_overdue:
                    # Get description from first item
                    description = ""
                    if doc.items_list and len(doc.items_list) > 0:
                        description = doc.items_list[0].name or ""

                    overdue.append({
                        "id": doc.id,
                        "invoice_number": f"{doc.number}{doc.numeration or ''}",
                        "customer_name": doc.entity.name if doc.entity else "Unknown",
                        "customer_email": doc.entity.email if doc.entity and doc.entity.email else "",
                        "amount_net": float(doc.amount_net) if doc.amount_net else 0,
                        "amount_gross": float(payment.amount) if payment.amount else 0,
                        "currency": "EUR",
                        "due_date": str(due),
                        "days_overdue": days_overdue,
                        "description": description,
                    })

    # Sort by days overdue (most overdue first)
    overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
    return overdue


def check_payment_status(invoice_number: str) -> dict:
    """Check the payment status of a specific invoice.

    Args:
        invoice_number: The invoice number (e.g., "1/2026").

    Returns:
        Dict with: invoice_number, status ('paid', 'not_paid', 'partial'),
        amount_due, amount_paid, due_date.
    """
    client = _get_client()
    company_id = _get_company_id()
    docs_api = fic.IssuedDocumentsApi(client)

    result = docs_api.list_issued_documents(
        company_id=company_id,
        type="invoice",
    )

    if not result.data:
        return {"invoice_number": invoice_number, "status": "not_found"}

    for doc in result.data:
        doc_num = f"{doc.number}{doc.numeration or ''}"
        if doc_num == invoice_number:
            if not doc.payments_list:
                return {"invoice_number": invoice_number, "status": "unknown"}

            total_due = 0
            total_paid = 0
            due_date = None

            for p in doc.payments_list:
                amt = float(p.amount) if p.amount else 0
                if p.status == "paid":
                    total_paid += amt
                else:
                    total_due += amt
                if p.due_date:
                    due_date = str(p.due_date)

            if total_due == 0:
                status = "paid"
            elif total_paid > 0:
                status = "partial"
            else:
                status = "not_paid"

            return {
                "invoice_number": invoice_number,
                "status": status,
                "amount_due": round(total_due, 2),
                "amount_paid": round(total_paid, 2),
                "due_date": due_date,
                "customer_name": doc.entity.name if doc.entity else "Unknown",
            }

    return {"invoice_number": invoice_number, "status": "not_found"}


def get_company_info() -> dict:
    """Get basic info about the connected company.

    Returns company name, VAT number, and contact details.
    """
    client = _get_client()
    company_id = _get_company_id()
    companies_api = fic.CompaniesApi(client)

    result = companies_api.get_company_info(company_id=company_id)
    info = result.data.company_info

    return {
        "name": info.name if info.name else "",
        "vat_number": info.vat_number if info.vat_number else "",
        "email": info.email if info.email else "",
        "phone": info.phone if info.phone else "",
    }
