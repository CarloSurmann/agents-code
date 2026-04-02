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
from proving_ground.providers.base import Invoice, Contact, CompanyInfo, PurchaseOrder, SupplierInvoiceData
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


# ---------------------------------------------------------------------------
# Supplier Invoice / AP data pools
# ---------------------------------------------------------------------------

_SUPPLIER_PRODUCTS = [
    {"description": "Steel beams — HEB 200", "unit_price": 85.00},
    {"description": "Copper wiring — 2.5mm²", "unit_price": 3.20},
    {"description": "Circuit breakers — 16A", "unit_price": 12.50},
    {"description": "Hydraulic pump — HP400", "unit_price": 1450.00},
    {"description": "Welding electrodes — pack of 100", "unit_price": 28.00},
    {"description": "Safety helmets — EN 397", "unit_price": 18.50},
    {"description": "Industrial lubricant — 20L", "unit_price": 67.00},
    {"description": "LED panel lights — 60x60cm", "unit_price": 42.00},
    {"description": "PVC pipes — 110mm × 3m", "unit_price": 9.80},
    {"description": "Concrete mix — 25kg bag", "unit_price": 4.50},
    {"description": "Consulting services — engineering review", "unit_price": 150.00},
    {"description": "Transport — flatbed delivery", "unit_price": 320.00},
    {"description": "Software license — CAD annual", "unit_price": 890.00},
    {"description": "Office supplies — paper A4 box", "unit_price": 32.00},
    {"description": "Cleaning services — monthly", "unit_price": 480.00},
]

_VAT_RATES = {
    "NL": 21.0, "DE": 19.0, "UK": 20.0, "IT": 22.0, "FR": 20.0,
}

_SUPPLIER_IBANS = {
    "NL": "NL91ABNA0417164300",
    "DE": "DE89370400440532013000",
    "UK": "GB29NWBK60161331926819",
    "IT": "IT60X0542811101000000123456",
    "FR": "FR7630006000011234567890189",
}

_SCENARIO_TYPES = [
    "EXACT_MATCH",
    "PRICE_DISCREPANCY",
    "QTY_DISCREPANCY",
    "NO_PO",
    "DUPLICATE",
    "CREDIT_NOTE",
    "NEW_SUPPLIER",
]


# ---------------------------------------------------------------------------
# Supplier invoice generators
# ---------------------------------------------------------------------------


def generate_supplier_invoice_data(
    count: int = 7,
    seed: int = 42,
    scenario_distribution: dict[str, float] | None = None,
) -> list[SupplierInvoiceData]:
    """Generate synthetic extracted invoice data for AP testing.

    By default generates one invoice per scenario type (7 total).
    When count > 7, distributes extras across scenarios using weights.
    """
    rng = random.Random(seed)

    dist = scenario_distribution or {s: 1.0 / len(_SCENARIO_TYPES) for s in _SCENARIO_TYPES}

    # Assign scenario types
    if count <= len(_SCENARIO_TYPES):
        scenarios = _SCENARIO_TYPES[:count]
    else:
        scenarios = list(_SCENARIO_TYPES)
        extras = count - len(_SCENARIO_TYPES)
        weighted = list(dist.keys())
        weights = [dist[k] for k in weighted]
        for _ in range(extras):
            scenarios.append(rng.choices(weighted, weights=weights, k=1)[0])

    invoices: list[SupplierInvoiceData] = []
    seen_numbers: set[str] = set()

    for i, scenario in enumerate(scenarios):
        company = rng.choice(_COMPANIES)
        country = company["country"]
        vat_rate = _VAT_RATES.get(country, 21.0)

        # Generate line items (2-5 per invoice)
        num_lines = rng.randint(2, 5)
        products = rng.sample(_SUPPLIER_PRODUCTS, min(num_lines, len(_SUPPLIER_PRODUCTS)))
        line_items = []
        for prod in products:
            qty = rng.randint(1, 100)
            unit_price = prod["unit_price"]
            line_total = round(qty * unit_price, 2)
            line_items.append({
                "description": prod["description"],
                "quantity": float(qty),
                "unit_price": unit_price,
                "vat_rate": vat_rate,
                "line_total": line_total,
            })

        subtotal = round(sum(li["line_total"] for li in line_items), 2)
        vat_amount = round(subtotal * vat_rate / 100, 2)
        total_amount = round(subtotal + vat_amount, 2)

        # Invoice number
        inv_num = f"INV-{company['code']}-{seed:02d}{i:02d}"

        # Handle DUPLICATE: reuse the previous invoice number and PO reference
        if scenario == "DUPLICATE" and invoices:
            original = invoices[0]  # duplicate of the first invoice
            inv_num = original.invoice_number
            po_ref = original.po_reference  # same PO as original
            # Keep same supplier and amounts for realistic duplicate
            company = next((c for c in _COMPANIES if c["name"] == original.supplier_name), company)
            line_items = [dict(li) for li in original.line_items]
            subtotal = original.subtotal
            vat_amount = original.vat_amount
            total_amount = original.total_amount

        # Handle CREDIT_NOTE: negative amounts
        if scenario == "CREDIT_NOTE":
            inv_num = f"CN-{company['code']}-{seed:02d}{i:02d}"
            for li in line_items:
                li["line_total"] = -abs(li["line_total"])
            subtotal = -abs(subtotal)
            vat_amount = -abs(vat_amount)
            total_amount = -abs(total_amount)

        # Handle NEW_SUPPLIER: use a company not in the standard pool
        supplier_name = company["name"]
        supplier_code = company["code"]
        supplier_vat = f"{country}123456789B{rng.randint(10, 99)}"
        if scenario == "NEW_SUPPLIER":
            supplier_name = f"NeueFirma-{rng.randint(100, 999)} GmbH"
            supplier_code = ""
            supplier_vat = f"DE{rng.randint(100000000, 999999999)}"

        # PO reference (not for NO_PO, CREDIT_NOTE, NEW_SUPPLIER)
        # DUPLICATE already set po_ref above; skip re-assignment
        if scenario == "DUPLICATE" and invoices:
            pass  # po_ref already set to original's PO reference
        elif scenario not in ("NO_PO", "CREDIT_NOTE", "NEW_SUPPLIER"):
            po_ref = f"PO-{seed:02d}{i:02d}"
        else:
            po_ref = None

        invoice_date = date.today() - timedelta(days=rng.randint(1, 10))
        due_date = invoice_date + timedelta(days=30)

        invoices.append(SupplierInvoiceData(
            message_id=f"ap_{seed}_{i}",
            extraction_confidence=0.95 if scenario != "NEW_SUPPLIER" else 0.78,
            supplier_name=supplier_name,
            supplier_vat=supplier_vat,
            supplier_address=f"Businesspark {rng.randint(1, 200)}, {country}",
            invoice_number=inv_num,
            invoice_date=invoice_date.isoformat(),
            due_date=due_date.isoformat(),
            payment_terms="Net 30",
            po_reference=po_ref,
            currency="EUR",
            line_items=line_items,
            subtotal=subtotal,
            vat_amount=vat_amount,
            total_amount=total_amount,
            iban=_SUPPLIER_IBANS.get(country, "NL91ABNA0417164300"),
            bic="ABNANL2A",
            payment_reference=inv_num,
            scenario_type=scenario,
        ))

    return invoices


def generate_purchase_orders(
    invoices: list[SupplierInvoiceData],
    seed: int = 42,
) -> list[PurchaseOrder]:
    """Derive POs from invoice data so matching relationships are deterministic.

    - EXACT_MATCH: PO matches invoice exactly
    - PRICE_DISCREPANCY: PO unit prices differ by >2%
    - QTY_DISCREPANCY: PO quantities differ (invoice has more)
    - DUPLICATE: PO exists for original invoice
    - NO_PO / CREDIT_NOTE / NEW_SUPPLIER: no PO generated
    """
    rng = random.Random(seed)
    pos: list[PurchaseOrder] = []
    seen_po_numbers: set[str] = set()

    for inv in invoices:
        if inv.scenario_type in ("NO_PO", "CREDIT_NOTE", "NEW_SUPPLIER"):
            continue

        if inv.po_reference is None:
            continue

        # Skip duplicate PO references (DUPLICATE scenario reuses first invoice's PO)
        if inv.po_reference in seen_po_numbers:
            continue
        seen_po_numbers.add(inv.po_reference)

        # Start with invoice line items as base
        po_lines = []
        for li in inv.line_items:
            po_line = dict(li)  # copy

            if inv.scenario_type == "PRICE_DISCREPANCY":
                # Make PO price 3-8% different (above the 2% tolerance)
                variance = rng.uniform(0.03, 0.08)
                direction = rng.choice([-1, 1])
                po_line["unit_price"] = round(li["unit_price"] * (1 + direction * variance), 2)
                po_line["line_total"] = round(po_line["quantity"] * po_line["unit_price"], 2)

            elif inv.scenario_type == "QTY_DISCREPANCY":
                # PO has fewer units than invoice (over-delivery)
                reduction = rng.randint(1, max(1, int(li["quantity"] * 0.3)))
                po_line["quantity"] = max(1.0, li["quantity"] - reduction)
                po_line["line_total"] = round(po_line["quantity"] * po_line["unit_price"], 2)

            po_lines.append(po_line)

        po_subtotal = round(sum(pl["line_total"] for pl in po_lines), 2)
        # PO total includes VAT to match invoice total_amount
        vat_rate = po_lines[0].get("vat_rate", 21.0) if po_lines else 21.0
        po_total = round(po_subtotal * (1 + vat_rate / 100), 2)
        order_date = date.fromisoformat(inv.invoice_date) - timedelta(days=rng.randint(5, 30))

        pos.append(PurchaseOrder(
            po_number=inv.po_reference,
            supplier_name=inv.supplier_name,
            supplier_code=next(
                (c["code"] for c in _COMPANIES if c["name"] == inv.supplier_name), ""
            ),
            order_date=order_date,
            total_amount=po_total,
            currency=inv.currency,
            status="open",
            line_items=po_lines,
            cumulative_invoiced=0.0,
        ))

    return pos


def generate_supplier_invoice_emails(
    invoices: list[SupplierInvoiceData],
    seed: int = 42,
) -> list[EmailMessage]:
    """Wrap SupplierInvoiceData into realistic EmailMessage objects for the mock inbox."""
    rng = random.Random(seed)
    from datetime import datetime, timezone

    emails: list[EmailMessage] = []
    for inv in invoices:
        # Find company for sender details
        company = next((c for c in _COMPANIES if c["name"] == inv.supplier_name), None)
        sender_email = company["email"] if company else f"invoices@{inv.supplier_name.lower().replace(' ', '')}.example.com"
        lang = company["lang"] if company else "en"

        # Pick a sender name
        first = rng.choice(_CONTACT_FIRST_NAMES.get(lang, _CONTACT_FIRST_NAMES["en"]))
        last = rng.choice(_CONTACT_LAST_NAMES.get(lang, _CONTACT_LAST_NAMES["en"]))

        # Build email body as text representation of the invoice
        line_summary = "\n".join(
            f"  - {li['description']}: {li['quantity']:.0f} × €{li['unit_price']:.2f} = €{abs(li['line_total']):.2f}"
            for li in inv.line_items
        )

        po_line = f"PO Reference: {inv.po_reference}\n" if inv.po_reference else ""

        prefix = "Credit Note" if inv.scenario_type == "CREDIT_NOTE" else "Invoice"
        subject = f"{prefix} {inv.invoice_number} from {inv.supplier_name}"

        body = (
            f"Dear Accounts Payable,\n\n"
            f"Please find attached {prefix.lower()} {inv.invoice_number}.\n\n"
            f"Supplier: {inv.supplier_name}\n"
            f"VAT: {inv.supplier_vat}\n"
            f"Invoice Date: {inv.invoice_date}\n"
            f"Due Date: {inv.due_date}\n"
            f"{po_line}"
            f"\nLine items:\n{line_summary}\n\n"
            f"Subtotal: €{abs(inv.subtotal):.2f}\n"
            f"VAT ({inv.line_items[0]['vat_rate'] if inv.line_items else 21}%): €{abs(inv.vat_amount):.2f}\n"
            f"Total: €{abs(inv.total_amount):.2f}\n\n"
            f"Payment to: {inv.iban} ({inv.bic})\n"
            f"Reference: {inv.payment_reference}\n\n"
            f"Kind regards,\n{first} {last}\n{inv.supplier_name}"
        )

        emails.append(EmailMessage(
            message_id=inv.message_id,
            thread_id=f"thread_{inv.message_id}",
            from_email=sender_email,
            from_name=f"{first} {last}",
            to_email="ap@company.example.com",
            to_name="Accounts Payable",
            subject=subject,
            body=body,
            date=datetime.now(timezone.utc),
            is_reply=False,
        ))

    return emails
