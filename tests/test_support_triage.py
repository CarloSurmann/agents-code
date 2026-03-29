#!/usr/bin/env python3
"""
End-to-end test for the G2 Customer Support Triage Agent.

Demonstrates:
1. Confidence routing (medium → HITL, then auto-promote after streak)
2. Feedback capture (every approve/edit/skip recorded)
3. KB search (FAQ matching + gap detection)
4. Business process monitors (volume spike, KB gaps)
5. Health report (approval rate, auto-promotion, drift)

Usage:
    cd agents-code
    python tests/test_support_triage.py
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

# Add agents-code to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from agency.channels.console import ConsoleChannel
from agency.tools.email.interface import EmailMessage
import agents.customer_support_triage as support_agent

# ─── Test data: 10 support emails ─────────────────────────────────────

_TO = "support@techwidget.com"
_TO_NAME = "TechWidget Support"

SAMPLE_EMAILS = [
    EmailMessage(
        message_id="msg-001",
        thread_id="thread-001",
        from_email="jan@customer.nl",
        from_name="Jan de Vries",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Where is my order #12847?",
        body="Hi, I placed order #12847 three days ago and still haven't received any shipping confirmation. Can you tell me when it will arrive? Thanks, Jan",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-002",
        thread_id="thread-002",
        from_email="maria@company.de",
        from_name="Maria Schmidt",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Does the Widget Pro work with USB-C?",
        body="Hello, I'm interested in buying the Widget Pro but I need to know if it supports USB-C connections. My laptop only has USB-C ports. Also, what's the warranty period? Thanks, Maria",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-003",
        thread_id="thread-003",
        from_email="piet@klant.nl",
        from_name="Piet Bakker",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Product arrived damaged!!",
        body="I just received my order and the Widget Pro is completely scratched and the box was crushed. This is unacceptable! I want a full refund or a replacement immediately. Order #12901.",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-004",
        thread_id="thread-004",
        from_email="sophie@bedrijf.be",
        from_name="Sophie Dubois",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="I was charged twice for my order",
        body="Hi, I just checked my bank statement and I see two charges of 49.95 EUR for order #12888. Please refund the duplicate charge. My IBAN is attached.",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-005",
        thread_id="thread-005",
        from_email="tom@startup.nl",
        from_name="Tom Hendriks",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="How do I set up the Widget Pro?",
        body="Hey, just got my Widget Pro but I can't figure out how to connect it to my computer. The manual is confusing. Can you walk me through the setup? Thanks!",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-006",
        thread_id="thread-006",
        from_email="lisa@shop.nl",
        from_name="Lisa van der Berg",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Can I change my delivery address?",
        body="Hi, I just placed order #12910 but I entered the wrong delivery address. Can you change it to: Keizersgracht 123, 1015 CJ Amsterdam? The order hasn't shipped yet.",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-007",
        thread_id="thread-007",
        from_email="erik@tech.nl",
        from_name="Erik Jansen",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Order status #12850",
        body="Could you check on order #12850? It was supposed to arrive yesterday but I have no tracking update since Monday.",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-008",
        thread_id="thread-008",
        from_email="anna@gmail.com",
        from_name="Anna Kowalski",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="What's your warranty policy?",
        body="Hi there, I bought a Widget last month and it stopped working. Is it still under warranty? How do I get it repaired or replaced?",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-009",
        thread_id="thread-009",
        from_email="mark@company.nl",
        from_name="Mark de Groot",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Need an invoice with VAT number",
        body="Hello, I purchased 5 Widgets for our company (order #12920) but the invoice doesn't show our VAT number NL123456789B01. Can you send a corrected invoice? We need it for our administration.",
        date=datetime.now(),
        is_reply=False,
    ),
    EmailMessage(
        message_id="msg-010",
        thread_id="thread-010",
        from_email="kim@webshop.be",
        from_name="Kim Peeters",
        to_email=_TO,
        to_name=_TO_NAME,
        subject="Tracking number not working",
        body="I got a tracking number for my order but when I enter it on the PostNL website it says 'not found'. Order #12905, tracking: 3STEST123456. It's been 3 days since I got the number.",
        date=datetime.now(),
        is_reply=False,
    ),
]


# ─── Main ──────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(__file__).parent.parent / "deployments" / "support-demo.yaml"

    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    print("=" * 60)
    print("  G2 — Customer Support Triage Agent")
    print("  Triage-and-Learn Demo")
    print("=" * 60)
    print()
    print("Features to observe:")
    print("  1. Confidence routing (auto-approve after streak)")
    print("  2. Feedback capture (every decision recorded)")
    print("  3. KB search (FAQ matching + gap detection)")
    print("  4. Business process monitors (volume, KB gaps)")
    print("  5. Health report at the end")
    print()
    print(f"Loading {len(SAMPLE_EMAILS)} sample support emails...")
    print()

    # Create agent with console channel
    channel = ConsoleChannel()
    agent, config, monitor_engine = support_agent.create_agent(str(config_path), channel=channel)

    # Seed mock provider with sample emails (access via module after create_agent initializes it)
    mock = support_agent._mock_provider
    if mock:
        # Use all emails — running with Claude API
        demo_emails = SAMPLE_EMAILS
        mock.seed_inbox(demo_emails)
        print(f"Seeded {len(demo_emails)} emails into mock inbox")
    else:
        print("WARNING: Mock provider not initialized!")
        sys.exit(1)

    print()
    print("-" * 60)
    print("Starting agent... It will process each email and ask for your approval.")
    print("Try mixing: approve some, edit one, skip one — see how feedback accumulates.")
    print("-" * 60)
    print()

    # Run the agent
    task = """Process all incoming support emails. For each email:
1. Read it fully with read_support_email
2. Classify it (category, urgency, sentiment)
3. Log the ticket with log_ticket
4. Search the knowledge base with search_kb
5. Draft and send a response with send_support_reply

Process ONE email at a time. After all emails are done, run run_monitors and get_health_report."""

    result = agent.run(task)

    # Print summary
    print()
    print("=" * 60)
    print("  Agent Run Complete")
    print("=" * 60)
    print(f"  Iterations: {result.iterations}")
    print(f"  Tool calls: {len(result.tool_calls)}")
    print(f"  Cost: ${result.cost_usd:.4f}")
    print()

    # Show feedback DB state
    try:
        from agency import feedback
        stats = feedback.get_feedback_stats(days=1)
        print("📊 Feedback Summary:")
        print(f"  Total decisions: {stats['total_decisions']}")
        print(f"  By action: {stats['by_action']}")
        print(f"  By category: {stats['by_category']}")
        print(f"  Auto-approved: {stats['auto_approved']}")
        print(f"  Promoted categories: {stats['promoted_categories']}")
    except Exception as e:
        print(f"  Could not read feedback stats: {e}")

    print()
    if result.trace_file:
        print(f"📝 Trace saved to: {result.trace_file}")


if __name__ == "__main__":
    main()
