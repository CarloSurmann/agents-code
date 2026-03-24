"""AR Follow-Up Agent — Invoice chasing workflow (B1).

This is the WIRING file. It assembles an agent from shared building blocks.
The actual intelligence lives in the system prompt and skills.

Two modes:
1. Conversational (default): runs via server.py, talks on Telegram
2. Batch: runs once, processes all overdue invoices, outputs results

Usage:
    # Conversational mode — use server.py instead:
    # python server.py

    # Batch mode (one-shot, for testing):
    python -m agents.ar_follow_up
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agency.agent import Agent
from agency.tracing import JSONTracer
from agency.tools.gmail import send_email, search_inbox, read_message


# ---------------------------------------------------------------------------
# Stub accounting tools (replace with exact_online when connected)
# ---------------------------------------------------------------------------

def get_overdue_invoices() -> list:
    """Fetch all overdue invoices from the accounting system.

    Returns a list of overdue invoices with: invoice_number, customer_name,
    customer_email, amount, currency, due_date, days_overdue, language.
    """
    from datetime import date, timedelta

    today = date.today()
    return [
        {
            "invoice_number": "INV-2024-042",
            "customer_name": "BuildRight BV",
            "customer_email": "accounts@buildright.example.com",
            "amount": 4250.00,
            "currency": "EUR",
            "due_date": str(today - timedelta(days=7)),
            "days_overdue": 7,
            "language": "nl",
        },
        {
            "invoice_number": "INV-2024-038",
            "customer_name": "TechCorp GmbH",
            "customer_email": "finance@techcorp.example.com",
            "amount": 12800.00,
            "currency": "EUR",
            "due_date": str(today - timedelta(days=21)),
            "days_overdue": 21,
            "language": "de",
        },
    ]


def check_payment_status(invoice_number: str) -> dict:
    """Check if a specific invoice has been paid.

    Returns the current status: 'overdue', 'paid', 'partial', or 'disputed'.
    """
    return {"invoice_number": invoice_number, "status": "overdue"}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an Accounts Receivable assistant for {company_name}.

Your job:
1. Review overdue invoices provided to you
2. Determine the appropriate chase stage based on days overdue
3. Draft a personalized chase email for each invoice
4. Send the email (subject to human approval)

Chase stages:
- 1-6 days: Friendly reminder. Assume it was an oversight. Casual tone.
- 7-13 days: Firm but warm. Ask for a payment date. Offer to resend invoice.
- 14-29 days: Formal. Reference previous reminders. Request urgent attention.
- 30+ days: Final notice. Serious tone. Mention potential consequences.

Rules:
- Be respectful — these are business relationships
- Never threaten legal action (that's a human decision)
- Always include: invoice number, amount, due date, payment details
- Write in the customer's language when specified
- If a customer has replied or disputed, flag it — don't chase blindly
"""


# ---------------------------------------------------------------------------
# Build agent
# ---------------------------------------------------------------------------

def build_agent(company_name: str = "ACME Corp", model: str = "ollama/qwen3.5:9b") -> Agent:
    """Build and return the AR Follow-Up agent for batch mode."""
    tracer = JSONTracer()

    return Agent(
        name="ar-follow-up",
        system_prompt=SYSTEM_PROMPT.format(company_name=company_name),
        model=model,
        tools=[
            send_email,
            search_inbox,
            read_message,
            get_overdue_invoices,
            check_payment_status,
        ],
        skills=[],  # TODO: add skill files
        hooks=[],   # No HITL in batch mode
        tracer=tracer,
    )


# ---------------------------------------------------------------------------
# Batch mode entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent = build_agent()

    print(f"Running AR Follow-Up agent (model: {agent.model})...")
    result = agent.run(
        "Fetch overdue invoices and draft chase emails for each one. "
        "Show me the emails you would send."
    )

    print(f"\n{'='*60}")
    print(f"Done — {result.iterations} iterations, ${result.cost_usd:.4f}")
    if result.trace_file:
        print(f"Trace: {result.trace_file}")
    print(f"{'='*60}\n")
    print(result.output)
