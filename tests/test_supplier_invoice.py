#!/usr/bin/env python3
"""
End-to-end test for the F1 Supplier Invoice Processing Agent.

Demonstrates:
1. PDF extraction from synthetic invoice data
2. PO matching (exact match, discrepancy, no match)
3. Duplicate detection
4. Confidence routing (auto-approve small matches, HITL for discrepancies)
5. Feedback capture + health report

Usage:
    cd agents-code
    python tests/test_supplier_invoice.py          # Interactive (ConsoleChannel)
    python tests/test_supplier_invoice.py --auto    # Automated (TestChannel + evals)
"""

import sys
import json
import logging
from pathlib import Path

# Add agents-code to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from agency.channels.console import ConsoleChannel
from proving_ground.providers.data_generators import (
    generate_supplier_invoice_data,
    generate_purchase_orders,
    generate_supplier_invoice_emails,
)
import agents.supplier_invoice_processing as ap_agent


# ─── Main ──────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    auto_mode = "--auto" in sys.argv
    config_path = Path(__file__).parent.parent / "deployments" / "ap-demo.yaml"

    if not config_path.exists():
        print(f"Config not found: {config_path}")
        sys.exit(1)

    print("=" * 60)
    print("  F1 — Supplier Invoice Processing Agent")
    print("  Synthetic Data E2E Test")
    print("=" * 60)
    print()

    # ─── Generate synthetic data ─────────────────────────────────
    print("Generating synthetic data...")
    invoices = generate_supplier_invoice_data(count=7, seed=42)
    purchase_orders = generate_purchase_orders(invoices, seed=42)
    emails = generate_supplier_invoice_emails(invoices, seed=42)

    print(f"  Invoices:        {len(invoices)}")
    print(f"  Purchase Orders: {len(purchase_orders)}")
    print(f"  Emails:          {len(emails)}")
    print()

    # Show scenario breakdown
    print("Scenario breakdown:")
    for inv in invoices:
        po_ref = inv.po_reference or "—"
        print(f"  {inv.scenario_type:<22} | {inv.invoice_number:<20} | EUR {abs(inv.total_amount):>10,.2f} | PO: {po_ref}")
    print()

    # ─── Create agent ────────────────────────────────────────────
    if auto_mode:
        from proving_ground.channels.test_channel import TestChannel
        channel = TestChannel(
            default_action="approve",
            per_tool_actions={"approve_invoice": "approve"},
        )
        print("Mode: AUTOMATED (TestChannel — auto-approve all)")
    else:
        channel = ConsoleChannel()
        print("Mode: INTERACTIVE (ConsoleChannel — you approve/reject)")
    print()

    agent, config, monitor_engine = ap_agent.create_agent(str(config_path), channel=channel)

    # ─── Seed synthetic data ─────────────────────────────────────
    # Seed mock email inbox
    mock = ap_agent._mock_provider
    if mock:
        mock.seed_inbox(emails)
        print(f"Seeded {len(emails)} emails into mock inbox")
    else:
        print("WARNING: Mock provider not initialized!")
        sys.exit(1)

    # Seed extraction data + POs
    ap_agent.seed_ap_data(invoices, purchase_orders)
    print(f"Seeded {len(invoices)} extraction records + {len(purchase_orders)} POs")
    print()

    print("-" * 60)
    print("Starting agent... It will process each invoice.")
    if not auto_mode:
        print("Try: approve matched invoices, reject discrepancies, see what happens.")
    print("-" * 60)
    print()

    # ─── Run the agent ───────────────────────────────────────────
    task = """Process all incoming supplier invoices. For each email:
1. Read it fully with read_invoice_email
2. Extract invoice data with extract_invoice_data
3. Check for duplicates with check_duplicate — if duplicate, log and skip to next
4. Match against purchase orders with match_purchase_order
5. If a PO was found, compare line items with compare_line_items
6. Log the invoice with log_invoice
7. If matched and not a credit note, approve with approve_invoice

Process ONE invoice at a time. After all invoices are done, run run_monitors and get_health_report."""

    result = agent.run(task)

    # ─── Print summary ───────────────────────────────────────────
    print()
    print("=" * 60)
    print("  Agent Run Complete")
    print("=" * 60)
    print(f"  Iterations:  {result.iterations}")
    print(f"  Tool calls:  {len(result.tool_calls)}")
    print(f"  Cost:        ${result.cost_usd:.4f}")
    print()

    # Show invoice DB state
    print("📄 Processed Invoices:")
    for key, inv in ap_agent._invoice_db.items():
        print(f"  {inv.get('invoice_number', '?'):<20} | {inv.get('status', '?'):<10} | "
              f"{inv.get('match_status', '?'):<15} | {inv.get('currency', 'EUR')} {inv.get('total_amount', 0):>10,.2f}")
    print()

    # Show feedback DB state
    try:
        from agency import feedback
        stats = feedback.get_feedback_stats(days=1)
        print("📊 Feedback Summary:")
        print(f"  Total decisions:      {stats['total_decisions']}")
        print(f"  By action:            {stats['by_action']}")
        print(f"  By category:          {stats['by_category']}")
        print(f"  Auto-approved:        {stats['auto_approved']}")
        print(f"  Promoted categories:  {stats['promoted_categories']}")
    except Exception as e:
        print(f"  Could not read feedback stats: {e}")

    print()
    if result.trace_file:
        print(f"📝 Trace saved to: {result.trace_file}")

    # ─── Run evals in auto mode ──────────────────────────────────
    if auto_mode and result.trace_file:
        print()
        print("=" * 60)
        print("  Running Eval Suite")
        print("=" * 60)
        print()

        from tests.evals_supplier_invoice import run_ap_eval_suite
        eval_results = run_ap_eval_suite(result.trace_file)

        passed = sum(1 for r in eval_results if r.passed)
        total = len(eval_results)
        print()
        print(f"  Result: {passed}/{total} checks passed")
        print(f"  {'PASS' if passed == total else 'FAIL'}")

    # Auto mode: also show TestChannel decisions
    if auto_mode and hasattr(channel, "get_decisions"):
        decisions = channel.get_decisions()
        if decisions:
            print()
            print("🤖 HITL Decisions (TestChannel):")
            for d in decisions:
                print(f"  {d.action:<10} | {d.source:<10} | {d.message_text[:80]}")


if __name__ == "__main__":
    main()
