"""Deterministic Test Data Factories — Generate realistic European business data.

All generators accept a `seed` parameter for reproducibility. Data pools are
hardcoded (no external API dependencies) and reflect typical European SMB
customers across NL, DE, IT, FR, and UK markets.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Sequence

from agency.tools.email.interface import EmailMessage
from proving_ground.providers.base import Invoice, Contact, CompanyInfo
from proving_ground.scenarios.clock import SimulatedClock


# ---------------------------------------------------------------------------
# Data pools — hardcoded European business data
# ---------------------------------------------------------------------------

_COMPANIES = [
    {"name": "BuildRight BV", "code": "BR001", "email": "accounts@buildright.example.com", "lang": "nl", "country": "NL"},
    {"name": "TechCorp GmbH", "code": "TC001", "email": "finance@techcorp.example.com", "lang": "de", "country": "DE"},
    {"name": "QuickShop Ltd", "code": "QS001", "email": "billing@quickshop.example.com", "lang": "en", "country": "UK"},
    {"name": "Costruzioni Rossi S.r.l.", "code": "CR001", "email": "contabilita@rossi.example.com", "lang": "it", "country": "IT"},
    {"name": "LogiTrans B.V.", "code": "LT001", "email": "ap@logitrans.example.com", "lang": "nl", "country": "NL"},
    {"name": "Schneider Maschinenbau AG", "code": "SM001", "email": "buchhaltung@schneider.example.com", "lang": "de", "country": "DE"},
    {"name": "Atlantic Foods Ltd", "code": "AF001", "email": "accounts@atlanticfoods.example.com", "lang": "en", "country": "UK"},
    {"name": "Dubois & Fils SARL", "code": "DF001", "email": "comptabilite@dubois.example.com", "lang": "fr", "country": "FR"},
    {"name": "Van der Berg Holding B.V.", "code": "VB001", "email": "crediteuren@vandenberg.example.com", "lang": "nl", "country": "NL"},
    {"name": "Bianchi Elettronica S.p.A.", "code": "BE001", "email": "pagamenti@bianchi.example.com", "lang": "it", "country": "IT"},
    {"name": "DigitalWave GmbH", "code": "DW001", "email": "rechnung@digitalwave.example.com", "lang": "de", "country": "DE"},
    {"name": "Northern Logistics PLC", "code": "NL001", "email": "finance@northernlogistics.example.com", "lang": "en", "country": "UK"},
]

_INVOICE_DESCRIPTIONS = [
    "Consulting services — Q1 2026",
    "Software license renewal",
    "Maintenance contract — monthly",
    "Project delivery milestone 2",
    "Hardware procurement — servers",
    "Training workshop — 2 days",
    "Support SLA — annual",
    "Custom development sprint 3",
    "Cloud infrastructure — March",
    "Design review and prototyping",
]

_CONTACT_FIRST_NAMES = {
    "nl": ["Jan", "Pieter", "Anke", "Willem", "Marieke"],
    "de": ["Hans", "Klaus", "Sabine", "Markus", "Katrin"],
    "en": ["James", "Sarah", "Robert", "Emma", "David"],
    "it": ["Marco", "Giulia", "Andrea", "Chiara", "Luca"],
    "fr": ["Pierre", "Marie", "Jean", "Isabelle", "Louis"],
}

_CONTACT_LAST_NAMES = {
    "nl": ["de Vries", "Jansen", "van den Berg", "Bakker", "Visser"],
    "de": ["Mueller", "Schmidt", "Weber", "Fischer", "Wagner"],
    "en": ["Smith", "Johnson", "Williams", "Brown", "Taylor"],
    "it": ["Rossi", "Ferrari", "Esposito", "Bianchi", "Romano"],
    "fr": ["Martin", "Bernard", "Dubois", "Moreau", "Laurent"],
}

_JOB_TITLES = [
    "Accounts Payable Manager",
    "Finance Director",
    "CFO",
    "Controller",
    "Bookkeeper",
    "Office Manager",
]

_SUPPORT_SUBJECTS = [
    "Invoice query — amount doesn't match PO",
    "Request for credit note",
    "Payment delay — cash flow issues",
    "Need a copy of invoice {inv}",
    "Dispute: services not delivered as agreed",
    "When will the refund be processed?",
    "Can we arrange a payment plan?",
    "Update our billing address",
    "VAT number incorrect on invoice",
    "Urgent: duplicate charge on account",
]


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

def generate_invoices(
    count: int = 10,
    seed: int = 42,
    clock: SimulatedClock | None = None,
    overdue_distribution: dict[str, float] | None = None,
) -> list[Invoice]:
    """Generate a list of realistic overdue invoices.

    Args:
        count: Number of invoices to generate.
        seed: Random seed for reproducibility.
        clock: Simulated clock (uses today for overdue calculation). Defaults to real date.
        overdue_distribution: Dict mapping overdue ranges to probabilities.
            Default: {"1-6": 0.3, "7-13": 0.25, "14-29": 0.25, "30+": 0.2}
    """
    rng = random.Random(seed)
    today = clock.today() if clock else date.today()

    dist = overdue_distribution or {
        "1-6": 0.3,
        "7-13": 0.25,
        "14-29": 0.25,
        "30+": 0.2,
    }

    def _pick_days_overdue() -> int:
        r = rng.random()
        cumulative = 0.0
        for range_str, prob in dist.items():
            cumulative += prob
            if r <= cumulative:
                if range_str.endswith("+"):
                    low = int(range_str[:-1])
                    return rng.randint(low, low + 60)
                parts = range_str.split("-")
                return rng.randint(int(parts[0]), int(parts[1]))
        return rng.randint(1, 30)

    invoices = []
    for i in range(count):
        company = rng.choice(_COMPANIES)
        days_overdue = _pick_days_overdue()
        due_date = today - timedelta(days=days_overdue)
        amount_net = round(rng.uniform(500, 25000), 2)
        amount_gross = round(amount_net * 1.21, 2)  # 21% VAT typical for EU

        invoices.append(Invoice(
            id=f"inv_{seed}_{i}",
            invoice_number=f"INV-2026-{seed:02d}{i:02d}",
            customer_name=company["name"],
            customer_email=company["email"],
            customer_code=company["code"],
            amount_net=amount_net,
            amount_gross=amount_gross,
            currency="EUR",
            due_date=due_date,
            days_overdue=days_overdue,
            description=rng.choice(_INVOICE_DESCRIPTIONS),
            status="not_paid",
            language=company["lang"],
        ))

    invoices.sort(key=lambda x: x.days_overdue, reverse=True)
    return invoices


def generate_contacts(
    customer_codes: Sequence[str] | None = None,
    seed: int = 42,
) -> dict[str, list[Contact]]:
    """Generate contact data for a set of customers.

    If customer_codes is None, generates contacts for all companies in the pool.
    """
    rng = random.Random(seed)

    if customer_codes is None:
        customer_codes = [c["code"] for c in _COMPANIES]

    contacts: dict[str, list[Contact]] = {}
    for code in customer_codes:
        # Find the company for language-appropriate names
        company = next((c for c in _COMPANIES if c["code"] == code), None)
        lang = company["lang"] if company else "en"
        domain = company["email"].split("@")[1] if company else "example.com"

        num_contacts = rng.randint(1, 3)
        company_contacts = []
        for _ in range(num_contacts):
            first = rng.choice(_CONTACT_FIRST_NAMES.get(lang, _CONTACT_FIRST_NAMES["en"]))
            last = rng.choice(_CONTACT_LAST_NAMES.get(lang, _CONTACT_LAST_NAMES["en"]))
            company_contacts.append(Contact(
                name=f"{first} {last}",
                email=f"{first.lower()}.{last.lower().replace(' ', '')}@{domain}",
                phone=f"+{rng.randint(31, 49)}{rng.randint(100000000, 999999999)}",
                job_title=rng.choice(_JOB_TITLES),
                customer_code=code,
            ))
        contacts[code] = company_contacts

    return contacts


def generate_support_emails(
    count: int = 10,
    seed: int = 42,
    categories: list[str] | None = None,
) -> list[EmailMessage]:
    """Generate realistic support email messages.

    Categories default to: billing_inquiry, payment_dispute, refund_request,
    address_update, general_inquiry.
    """
    rng = random.Random(seed)
    from datetime import datetime, timezone

    cats = categories or ["billing_inquiry", "payment_dispute", "refund_request", "address_update", "general_inquiry"]

    emails = []
    for i in range(count):
        company = rng.choice(_COMPANIES)
        lang = company["lang"]
        first = rng.choice(_CONTACT_FIRST_NAMES.get(lang, _CONTACT_FIRST_NAMES["en"]))
        last = rng.choice(_CONTACT_LAST_NAMES.get(lang, _CONTACT_LAST_NAMES["en"]))
        subject_template = rng.choice(_SUPPORT_SUBJECTS)
        subject = subject_template.format(inv=f"INV-2026-{rng.randint(100, 999)}")

        body = (
            f"Dear team,\n\n"
            f"I am writing regarding {subject.lower()}.\n"
            f"Our customer code is {company['code']}.\n"
            f"Please advise on next steps.\n\n"
            f"Best regards,\n{first} {last}\n{company['name']}"
        )

        emails.append(EmailMessage(
            message_id=f"support_{seed}_{i}",
            thread_id=f"thread_{seed}_{i}",
            from_email=f"{first.lower()}.{last.lower().replace(' ', '')}@{company['email'].split('@')[1]}",
            from_name=f"{first} {last}",
            to_email="support@agency.example.com",
            to_name="Support Team",
            subject=subject,
            body=body,
            date=datetime.now(timezone.utc),
            is_reply=False,
        ))

    return emails
