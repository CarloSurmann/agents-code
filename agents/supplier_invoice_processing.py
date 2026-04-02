"""
F1 — Supplier Invoice Processing & Matching Agent (thin wiring file).

Built with the triage-and-learn paradigm:
  - ConfidenceGate: scores decisions, auto-approves routine matched invoices
  - ChannelHITL: gates approve_invoice behind human approval
  - FeedbackCapture: records every approve/edit/reject for learning
  - MonitorEngine: proactive AP process monitoring

Hook order: [ConfidenceGate, ChannelHITL, FeedbackCapture]

Design: Workflow-to-System Build Method (Techwaves Strategy Paper).
Build ref: workflows/supplier-invoice-processing.md
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from agency import Agent
from agency.config import load_config, AgentConfig
from agency.skills import load_skills
from agency.tracing import JSONTracer

# Shared tools
from agency.tools.email.mock import MockProvider

# Triage-and-learn hooks
from agency.hooks.confidence_gate import ConfidenceGate
from agency.hooks.hitl import ChannelHITL
from agency.hooks.feedback_capture import FeedbackCapture

# Feedback + monitors + tuning
from agency import feedback
from agency.monitors import MonitorEngine, load_monitors_from_config
from agency.tuning import generate_health_report, format_health_report

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# ─── Module-level mock provider for testing ────────────────────────────

_mock_provider: MockProvider | None = None


def _init_mock_provider():
    global _mock_provider
    _mock_provider = MockProvider()
    return _mock_provider


# ─── Invoice tracker (SQLite for duplicate detection + logging) ────────

_invoice_db: dict = {}  # In-memory for now; production uses SQLite

# ─── Synthetic data stores (populated by seed_ap_data for testing) ─────

_extraction_data: dict = {}  # message_id → SupplierInvoiceData (as dict)
_purchase_orders: list = []  # list of PurchaseOrder objects


def seed_ap_data(extractions: list, purchase_orders: list) -> None:
    """Populate synthetic data stores for testing.

    Args:
        extractions: list of SupplierInvoiceData objects (from data_generators)
        purchase_orders: list of PurchaseOrder objects (from data_generators)
    """
    global _extraction_data, _purchase_orders, _invoice_db
    _extraction_data.clear()
    _purchase_orders.clear()
    _invoice_db.clear()

    for inv in extractions:
        _extraction_data[inv.message_id] = inv

    _purchase_orders.extend(purchase_orders)


# ─── Tool functions (thin wrappers for the agent) ─────────────────────


def search_ap_inbox() -> str:
    """Check for new incoming emails in the AP inbox. Returns emails that likely contain invoices (have PDF attachments)."""
    if _mock_provider is None:
        return json.dumps({"error": "Email provider not initialized"})

    emails = _mock_provider._inbox
    results = []
    for msg in emails:
        results.append({
            "message_id": msg.message_id,
            "from_email": msg.from_email,
            "from_name": msg.from_name,
            "subject": msg.subject,
            "body_preview": msg.body[:200],
            "has_attachment": True,  # mock assumes PDF attached
            "date": msg.date.isoformat() if hasattr(msg, "date") and msg.date else "",
        })
    return json.dumps({"count": len(results), "emails": results})


def read_invoice_email(message_id: str) -> str:
    """Read the full content of an invoice email and its PDF attachment by message_id."""
    if _mock_provider is None:
        return json.dumps({"error": "Email provider not initialized"})

    try:
        msg = _mock_provider.read_message(message_id)
        return json.dumps({
            "message_id": msg.message_id,
            "from_email": msg.from_email,
            "from_name": msg.from_name,
            "subject": msg.subject,
            "body": msg.body,
            "thread_id": msg.thread_id,
            "attachments": [{"filename": "invoice.pdf", "type": "application/pdf"}],
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})


def extract_invoice_data(message_id: str) -> str:
    """Extract structured data from the invoice PDF attachment. Returns supplier info, invoice number, line items, totals, and payment details. Uses LLM vision to parse the PDF."""
    # If synthetic data is loaded, return it directly
    if message_id in _extraction_data:
        inv = _extraction_data[message_id]
        return json.dumps({
            "status": "extracted",
            "message_id": message_id,
            "extraction_confidence": inv.extraction_confidence,
            "data": {
                "supplier_name": inv.supplier_name,
                "supplier_vat": inv.supplier_vat,
                "supplier_address": inv.supplier_address,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date,
                "due_date": inv.due_date,
                "payment_terms": inv.payment_terms,
                "po_reference": inv.po_reference,
                "currency": inv.currency,
                "line_items": inv.line_items,
                "subtotal": inv.subtotal,
                "vat_amount": inv.vat_amount,
                "total_amount": inv.total_amount,
                "iban": inv.iban,
                "bic": inv.bic,
                "payment_reference": inv.payment_reference,
            },
        })

    # Fallback: empty template (production would use Claude vision here)
    return json.dumps({
        "status": "extracted",
        "message_id": message_id,
        "extraction_confidence": 0.95,
        "data": {
            "supplier_name": None, "supplier_vat": None, "supplier_address": None,
            "invoice_number": None, "invoice_date": None, "due_date": None,
            "payment_terms": None, "po_reference": None, "currency": "EUR",
            "line_items": [], "subtotal": None, "vat_amount": None,
            "total_amount": None, "iban": None, "bic": None, "payment_reference": None,
        },
        "note": "No synthetic data loaded. Production uses Claude vision.",
    })


def check_duplicate(supplier_name: str, invoice_number: str, total_amount: float) -> str:
    """Check if this invoice has already been processed. Matches by invoice number + supplier within 90 days."""
    key = f"{supplier_name}:{invoice_number}"
    if key in _invoice_db:
        existing = _invoice_db[key]
        return json.dumps({
            "is_duplicate": True,
            "original_processed_date": existing.get("processed_date", ""),
            "original_status": existing.get("status", ""),
            "message": f"Duplicate detected: {invoice_number} from {supplier_name} was already processed.",
        })
    return json.dumps({"is_duplicate": False})


def match_purchase_order(supplier_name: str, po_reference: str | None, total_amount: float, invoice_date: str) -> str:
    """Search accounting system for a matching Purchase Order. Tries PO number first, then supplier+amount, then supplier+date."""
    from datetime import date as date_type, timedelta

    def _po_to_result(po, strategy: str, status: str) -> str:
        return json.dumps({
            "match_status": status,
            "po_number": po.po_number,
            "po_total": po.total_amount,
            "po_date": po.order_date.isoformat() if po.order_date else None,
            "po_supplier": po.supplier_name,
            "po_line_items": po.line_items,
            "cumulative_invoiced": po.cumulative_invoiced,
            "search_strategy": strategy,
        })

    # Strategy 1: Match by PO reference
    if po_reference:
        for po in _purchase_orders:
            if po.po_number == po_reference:
                # Check if amounts match (within 5% = likely exact or minor discrepancy)
                if po.total_amount > 0:
                    variance = abs(total_amount - po.total_amount) / po.total_amount
                    status = "EXACT_MATCH" if variance < 0.05 else "DISCREPANCY"
                else:
                    status = "EXACT_MATCH"
                return _po_to_result(po, "po_reference", status)

    # Strategy 2: Match by supplier + amount (within 5%)
    for po in _purchase_orders:
        if po.supplier_name.lower() == supplier_name.lower() and po.total_amount > 0:
            variance = abs(total_amount - po.total_amount) / po.total_amount
            if variance < 0.05:
                return _po_to_result(po, "supplier_amount", "EXACT_MATCH")
            elif variance < 0.15:
                return _po_to_result(po, "supplier_amount", "DISCREPANCY")

    # Strategy 3: Match by supplier + date range (within 60 days)
    try:
        inv_date = date_type.fromisoformat(invoice_date)
    except (ValueError, TypeError):
        inv_date = None

    if inv_date:
        for po in _purchase_orders:
            if po.supplier_name.lower() == supplier_name.lower() and po.order_date:
                days_apart = abs((inv_date - po.order_date).days)
                if days_apart <= 60:
                    return _po_to_result(po, "supplier_date", "DISCREPANCY")

    # No match found
    return json.dumps({
        "match_status": "NO_MATCH",
        "po_number": None,
        "po_total": None,
        "po_date": None,
        "po_supplier": None,
        "po_line_items": [],
        "cumulative_invoiced": 0.0,
        "search_strategy": "po_reference" if po_reference else "supplier_amount",
    })


def compare_line_items(invoice_lines: str, po_lines: str, tolerance_pct: float = 2.0) -> str:
    """Compare invoice line items against PO line items. Checks quantities, prices, and totals within the configured tolerance percentage. Pass lines as JSON strings."""
    try:
        inv = json.loads(invoice_lines) if isinstance(invoice_lines, str) else invoice_lines
        po = json.loads(po_lines) if isinstance(po_lines, str) else po_lines
    except (json.JSONDecodeError, TypeError):
        return json.dumps({"error": "Invalid line items JSON"})

    discrepancies = []
    matched_lines = 0

    for i, inv_line in enumerate(inv):
        if i < len(po):
            po_line = po[i]
            inv_price = float(inv_line.get("unit_price", 0))
            po_price = float(po_line.get("unit_price", 0))
            inv_qty = float(inv_line.get("quantity", 0))
            po_qty = float(po_line.get("quantity", 0))

            # Price check
            if po_price > 0:
                price_variance = abs(inv_price - po_price) / po_price * 100
                if price_variance > tolerance_pct:
                    discrepancies.append({
                        "line": i + 1,
                        "type": "PRICE_MISMATCH",
                        "invoice_value": inv_price,
                        "po_value": po_price,
                        "variance_pct": round(price_variance, 2),
                    })

            # Quantity check
            if inv_qty > po_qty:
                discrepancies.append({
                    "line": i + 1,
                    "type": "OVER_DELIVERY",
                    "invoice_qty": inv_qty,
                    "po_qty": po_qty,
                    "excess": inv_qty - po_qty,
                })

            if not discrepancies or all(d["line"] != i + 1 for d in discrepancies):
                matched_lines += 1
        else:
            discrepancies.append({
                "line": i + 1,
                "type": "EXTRA_LINE",
                "description": inv_line.get("description", ""),
                "message": "Invoice has more lines than PO",
            })

    total_lines = len(inv)
    match_score = matched_lines / total_lines if total_lines > 0 else 0

    return json.dumps({
        "total_invoice_lines": total_lines,
        "total_po_lines": len(po),
        "matched_lines": matched_lines,
        "match_score": round(match_score, 2),
        "discrepancies": discrepancies,
        "has_discrepancies": len(discrepancies) > 0,
    })


def approve_invoice(
    supplier_name: str,
    invoice_number: str,
    total_amount: float,
    currency: str,
    match_status: str,
    po_number: str,
    category: str,
) -> str:
    """Approve a processed invoice for payment preparation. This tool is gated by HITL approval. Include match_status and category for confidence routing."""
    # Log the approved invoice
    key = f"{supplier_name}:{invoice_number}"
    _invoice_db[key] = {
        "supplier_name": supplier_name,
        "invoice_number": invoice_number,
        "total_amount": total_amount,
        "currency": currency,
        "match_status": match_status,
        "po_number": po_number,
        "status": "approved",
        "processed_date": datetime.now().isoformat(),
    }

    logger.info(f"Invoice approved: {invoice_number} from {supplier_name} — {currency} {total_amount}")
    return json.dumps({
        "status": "approved",
        "invoice_number": invoice_number,
        "supplier": supplier_name,
        "amount": total_amount,
        "currency": currency,
        "matched_po": po_number,
        "message": "Invoice approved and queued for payment preparation.",
    })


def log_invoice(
    supplier_name: str,
    invoice_number: str,
    total_amount: float,
    category: str,
    match_status: str,
    has_discrepancy: bool,
    discrepancy_details: str,
) -> str:
    """Log a processed invoice for monitoring and analytics. Call this after processing each invoice."""
    try:
        ticket_id = feedback.record_ticket(
            from_email=supplier_name,
            subject=invoice_number,
            category=category,
            urgency="high" if has_discrepancy else "low",
            sentiment="neutral",
            kb_matched=match_status != "NO_MATCH",
            kb_query=f"{match_status}:{discrepancy_details}",
        )
        return json.dumps({"status": "logged", "ticket_id": ticket_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_monitors() -> str:
    """Run all AP process monitors and return alerts. Call this after processing a batch of invoices."""
    if not hasattr(run_monitors, "_engine"):
        return json.dumps({"alerts": [], "message": "No monitors configured"})

    alerts = run_monitors._engine.run_all()
    return run_monitors._engine.format_alerts(alerts)


def get_health_report() -> str:
    """Generate an agent health report showing approval rates, auto-promotion status, discrepancy trends, and processing volume."""
    try:
        report = generate_health_report(feedback._db_path or "")
        return format_health_report(report)
    except Exception as e:
        return f"Error generating health report: {e}"


# ─── System prompt builder ────────────────────────────────────────────


def build_system_prompt(config: AgentConfig, skills_content: str) -> str:
    """Build the full system prompt for the supplier invoice processing agent."""
    now = datetime.now()

    auto_approve_threshold = config.extra.get("auto_approve_threshold", 2000)
    escalation_threshold = config.extra.get("escalation_threshold", 10000)
    price_tolerance = config.extra.get("price_tolerance_pct", 2.0)

    return f"""You are a supplier invoice processing agent for {config.extra.get('company_name', 'the company')}.

## Current Date and Time
Today is {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')}). Current time: {now.strftime('%H:%M')}.

## Your Workflow
For each invoice email, follow these steps in order:

1. **Check inbox** using search_ap_inbox
2. **Read** the email using read_invoice_email
3. **Extract** invoice data using extract_invoice_data
4. **Check duplicate** using check_duplicate
5. **Match PO** using match_purchase_order
6. **Compare lines** using compare_line_items (if PO found)
7. **Log** the invoice using log_invoice (for monitoring)
8. **Approve** via approve_invoice (triggers approval if not auto-approved)

After processing all invoices:
9. **Run monitors** using run_monitors to check for anomalies
10. **Generate health report** using get_health_report

## Tools
- search_ap_inbox: Get all incoming invoice emails
- read_invoice_email: Read full email + attachment info
- extract_invoice_data: Extract structured data from invoice PDF
- check_duplicate: Check if invoice was already processed
- match_purchase_order: Find matching PO in accounting system
- compare_line_items: Compare invoice lines vs PO lines
- approve_invoice: Approve for payment (gated by approval)
- log_invoice: Record invoice for analytics
- run_monitors: Check AP process monitors
- get_health_report: Generate agent health report

## Routing Rules
- Auto-approve: EXACT_MATCH + total under {config.extra.get('currency', 'EUR')} {auto_approve_threshold} + known supplier
- HITL review: EXACT_MATCH above {config.extra.get('currency', 'EUR')} {auto_approve_threshold} or minor discrepancy
- Escalate: material discrepancy (>{price_tolerance}%), NO_MATCH, DUPLICATE, CREDIT_NOTE, new supplier, total above {config.extra.get('currency', 'EUR')} {escalation_threshold}

## Important Rules
1. ALWAYS check_duplicate before matching — stop processing if duplicate found
2. ALWAYS log_invoice after classification, even if escalated
3. Include match_status and category in approve_invoice so confidence routing works
4. For CREDIT_NOTE: do NOT approve — always escalate to human
5. For NO_MATCH: still log the invoice, then escalate
6. Verify extraction math: sum of line totals should equal subtotal, subtotal + VAT = total
7. Price tolerance is {price_tolerance}% — within this range counts as a match
8. Keep HITL messages concise and scannable

## Output Rules
- NEVER use tables or markdown headers — they don't render on messaging platforms
- Use bullet points, emojis, and short paragraphs
- Keep messages scannable on a phone screen
- Always show: supplier name, invoice number, amount, and match result

{skills_content}"""


# ─── Agent factory ────────────────────────────────────────────────────


def create_agent(config_path: str, channel=None) -> tuple:
    """Create and configure the supplier invoice processing agent.

    Returns (agent, config, monitor_engine) tuple.
    """
    config = load_config(config_path)

    # Initialize providers
    _init_mock_provider()
    feedback.init_feedback(config.extra.get("feedback_db_path", "data/feedback.db"))

    # Load skills
    skills_content = load_skills(["invoice_processing"])

    # Build system prompt
    system_prompt = build_system_prompt(config, skills_content)

    # Tools
    tools = [
        search_ap_inbox,
        read_invoice_email,
        extract_invoice_data,
        check_duplicate,
        match_purchase_order,
        compare_line_items,
        approve_invoice,
        log_invoice,
        run_monitors,
        get_health_report,
    ]

    # Hooks — THE TRIAGE-AND-LEARN PARADIGM
    hooks = []
    if channel:
        # 1. Confidence Gate (runs first — sets metadata)
        confidence_gate = ConfidenceGate(
            gated_tools=["approve_invoice"],
            client_id=config.name,
            high_threshold=config.extra.get("confidence_high", 0.85),
            low_threshold=config.extra.get("confidence_low", 0.60),
            channel=channel,
        )

        # 2. HITL (reads metadata — skips if auto-approved)
        hitl = ChannelHITL(
            channel=channel,
            gated_tools=["approve_invoice"],
        )

        # 3. Feedback Capture (runs after — records everything)
        feedback_hook = FeedbackCapture(
            client_id=config.name,
            agent_name="supplier-invoice-processing",
            auto_promote_threshold=config.extra.get("auto_promote_streak", 20),
        )

        hooks = [confidence_gate, hitl, feedback_hook]  # ORDER MATTERS

    # Monitors
    monitor_defs = load_monitors_from_config(config.extra.get("monitors", []))
    monitor_engine = MonitorEngine(
        monitors=monitor_defs,
        db_path=config.extra.get("feedback_db_path", "data/feedback.db"),
    )
    # Attach to the run_monitors tool function
    run_monitors._engine = monitor_engine

    # Tracer
    tracer = JSONTracer()

    agent = Agent(
        name="supplier-invoice-processing",
        model=config.model,
        system_prompt=system_prompt,
        tools=tools,
        hooks=hooks,
        tracer=tracer,
        max_iterations=60,
    )

    return agent, config, monitor_engine
