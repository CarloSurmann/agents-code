"""Exact Online tools — fetch invoices and check payment status.

Uses the ossobv/exactonline Python library for OAuth2 and API access.

Setup:
1. Sign up for Exact Online developer account
2. Register an app in the App Center → get client_id and client_secret
3. Set environment variables:
   - EXACT_CLIENT_ID
   - EXACT_CLIENT_SECRET
   - EXACT_DIVISION  (your company division number)
4. On first run, it will open a browser for OAuth authorization

API reference:
- ReceivablesList: outstanding invoices with amounts and days overdue
- SalesInvoices: invoice details (number, customer, dates)
- Accounts: customer/company records

Rate limits: 60 calls/minute, 5000/day per app-per-company.
"""

from __future__ import annotations

import json
import os
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_EXACT_BASE_URLS = {
    "nl": "https://start.exactonline.nl",
    "be": "https://start.exactonline.be",
    "de": "https://start.exactonline.de",
    "uk": "https://start.exactonline.co.uk",
}

_TOKEN_FILE = Path(os.environ.get("EXACT_TOKEN_PATH", "exact_token.json"))


# ---------------------------------------------------------------------------
# Auth & client
# ---------------------------------------------------------------------------

class _ExactClient:
    """Thin wrapper around Exact Online REST API.

    Handles OAuth2 token management (access token refresh, single-use
    refresh token rotation) and provides methods for the endpoints
    we actually need.
    """

    def __init__(self):
        self.client_id = os.environ.get("EXACT_CLIENT_ID", "")
        self.client_secret = os.environ.get("EXACT_CLIENT_SECRET", "")
        self.division = os.environ.get("EXACT_DIVISION", "")
        self.country = os.environ.get("EXACT_COUNTRY", "nl")
        self.base_url = _EXACT_BASE_URLS.get(self.country, _EXACT_BASE_URLS["nl"])

        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load saved tokens from disk."""
        if _TOKEN_FILE.exists():
            data = json.loads(_TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")

    def _save_tokens(self) -> None:
        """Save tokens to disk (refresh tokens are single-use!)."""
        _TOKEN_FILE.write_text(json.dumps({
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
        }))

    def _refresh(self) -> None:
        """Refresh the access token using the refresh token."""
        import httpx

        if not self._refresh_token:
            raise RuntimeError(
                "No refresh token available. Run the OAuth flow first. "
                "See: https://support.exactonline.com/community/s/article/"
                "All-All-DNO-Content-oauth-eol-oauth-devstep3"
            )

        response = httpx.post(
            f"{self.base_url}/api/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()

        # IMPORTANT: Exact gives a NEW refresh token each time (single-use!)
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._save_tokens()

    def _get(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Make an authenticated GET request to Exact Online API."""
        import httpx

        if not self._access_token:
            self._refresh()

        url = f"{self.base_url}/api/v1/{self.division}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

        response = httpx.get(url, headers=headers, params=params or {})

        # Token expired — refresh and retry once
        if response.status_code == 401:
            self._refresh()
            headers["Authorization"] = f"Bearer {self._access_token}"
            response = httpx.get(url, headers=headers, params=params or {})

        response.raise_for_status()
        data = response.json()

        # Exact wraps results in d.results (OData format)
        if isinstance(data, dict):
            return data.get("d", {}).get("results", [])
        return data


# Singleton client
_client: _ExactClient | None = None


def _get_client() -> _ExactClient:
    global _client
    if _client is None:
        _client = _ExactClient()
    return _client


# ---------------------------------------------------------------------------
# Tools (these are what agents call)
# ---------------------------------------------------------------------------

def get_overdue_invoices(min_amount: float = 0.0) -> list:
    """Fetch all overdue invoices from Exact Online.

    Returns a list of overdue invoices with: invoice_number, customer_name,
    customer_email, amount, currency, due_date, days_overdue.

    Use min_amount to filter out small invoices (e.g. min_amount=50 to skip
    invoices under €50).
    """
    client = _get_client()

    # ReceivablesList gives us outstanding amounts directly
    receivables = client._get(
        "read/financial/ReceivablesList",
        params={
            "$select": (
                "AccountCode,AccountName,Amount,CurrencyCode,"
                "Description,DueDate,InvoiceNumber,YourRef"
            ),
            "$filter": "DueDate lt datetime'" + date.today().isoformat() + "'",
        },
    )

    today = date.today()
    invoices = []

    for r in receivables:
        amount = float(r.get("Amount", 0))
        if amount < min_amount:
            continue

        due_date_str = r.get("DueDate", "")
        # Exact returns dates as "/Date(1234567890000)/" or ISO format
        due_date = _parse_exact_date(due_date_str)
        days_overdue = (today - due_date).days if due_date else 0

        invoices.append({
            "invoice_number": r.get("InvoiceNumber", ""),
            "customer_name": r.get("AccountName", ""),
            "customer_code": r.get("AccountCode", ""),
            "amount": amount,
            "currency": r.get("CurrencyCode", "EUR"),
            "due_date": str(due_date) if due_date else due_date_str,
            "days_overdue": days_overdue,
            "description": r.get("Description", ""),
            "reference": r.get("YourRef", ""),
        })

    # Sort by days overdue (most overdue first)
    invoices.sort(key=lambda x: x["days_overdue"], reverse=True)
    return invoices


def check_payment_status(invoice_number: str) -> dict:
    """Check the payment status of a specific invoice.

    Returns status: 'overdue', 'paid', 'partial', or 'unknown'.
    """
    client = _get_client()

    receivables = client._get(
        "read/financial/ReceivablesList",
        params={
            "$filter": f"InvoiceNumber eq '{invoice_number}'",
            "$select": "Amount,InvoiceNumber,DueDate",
        },
    )

    if not receivables:
        return {"invoice_number": invoice_number, "status": "paid"}

    amount = float(receivables[0].get("Amount", 0))
    if amount <= 0:
        return {"invoice_number": invoice_number, "status": "paid"}

    return {
        "invoice_number": invoice_number,
        "status": "overdue",
        "outstanding_amount": amount,
    }


def get_customer_contacts(customer_code: str) -> list:
    """Get contact details for a customer by their account code.

    Returns email addresses and contact names for sending chase emails.
    """
    client = _get_client()

    contacts = client._get(
        "crm/Contacts",
        params={
            "$filter": f"Account/Code eq '{customer_code}'",
            "$select": "FirstName,LastName,Email,Phone,JobTitleDescription",
        },
    )

    return [
        {
            "name": f"{c.get('FirstName', '')} {c.get('LastName', '')}".strip(),
            "email": c.get("Email", ""),
            "phone": c.get("Phone", ""),
            "job_title": c.get("JobTitleDescription", ""),
        }
        for c in contacts
        if c.get("Email")
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_exact_date(date_str: str) -> date | None:
    """Parse Exact Online date formats.

    Exact returns dates in two formats:
    - OData: "/Date(1234567890000)/" (milliseconds since epoch)
    - ISO: "2026-03-24"
    """
    if not date_str:
        return None

    # OData format: /Date(1234567890000)/
    if date_str.startswith("/Date("):
        try:
            ms = int(date_str.replace("/Date(", "").replace(")/", ""))
            return datetime.fromtimestamp(ms / 1000).date()
        except (ValueError, OSError):
            return None

    # ISO format
    try:
        return date.fromisoformat(date_str[:10])
    except ValueError:
        return None
