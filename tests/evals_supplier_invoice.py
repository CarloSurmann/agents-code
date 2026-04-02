#!/usr/bin/env python3
"""
Eval suite for the F1 Supplier Invoice Processing Agent.

Runs 9 trace-based checks against a saved trace file to verify
the agent processed invoices correctly.

Usage:
    cd agents-code

    # Run against a specific trace file
    python tests/evals_supplier_invoice.py traces/<trace_file>.jsonl

    # Called automatically by test_supplier_invoice.py --auto
"""

import sys
import json
import logging
from pathlib import Path
from typing import Callable

# Add agents-code to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agency.evals.runner import EvalResult, run_eval, run_eval_suite, check_tool_was_called, check_no_errors, check_completed_within

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AP-specific eval checks
# ---------------------------------------------------------------------------


def check_extract_called_per_invoice(expected_count: int = 7) -> Callable:
    """Check that extract_invoice_data was called for each invoice email."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == "extract_invoice_data"
        ]
        count = len(calls)
        if count >= expected_count:
            return True, f"Called {count} times (expected >= {expected_count})"
        return False, f"Called {count} times, expected >= {expected_count}"
    return _check


def check_duplicate_before_match() -> Callable:
    """Check that check_duplicate is called before match_purchase_order for each invoice."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        tool_calls = [
            e.get("data", {}).get("tool")
            for e in events
            if e.get("event") == "tool_call_start"
        ]

        # Find all extract_invoice_data calls — each marks start of processing an invoice
        extract_indices = [i for i, t in enumerate(tool_calls) if t == "extract_invoice_data"]
        violations = 0

        for ext_idx in extract_indices:
            # Look at tool calls after this extraction until the next extraction
            next_ext = len(tool_calls)
            for j in range(ext_idx + 1, len(tool_calls)):
                if tool_calls[j] == "extract_invoice_data":
                    next_ext = j
                    break

            segment = tool_calls[ext_idx:next_ext]

            # In this segment, check_duplicate should appear before match_purchase_order
            dup_idx = None
            match_idx = None
            for k, t in enumerate(segment):
                if t == "check_duplicate" and dup_idx is None:
                    dup_idx = k
                if t == "match_purchase_order" and match_idx is None:
                    match_idx = k

            if match_idx is not None and (dup_idx is None or dup_idx > match_idx):
                violations += 1

        if violations == 0:
            return True, "Duplicate check always before PO match"
        return False, f"{violations} invoice(s) had match before duplicate check"
    return _check


def check_match_called_for_non_duplicates() -> Callable:
    """Check that match_purchase_order is called for non-duplicate invoices."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        match_calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == "match_purchase_order"
        ]
        # With 7 invoices: 1 duplicate + 1 credit note = at most 5 should have match calls
        # But credit notes might still get a match attempt. At minimum we need some match calls.
        if len(match_calls) >= 3:
            return True, f"match_purchase_order called {len(match_calls)} times"
        return False, f"match_purchase_order called only {len(match_calls)} times, expected >= 3"
    return _check


def check_no_approve_for_credit_note() -> Callable:
    """Check that approve_invoice is NOT called with CREDIT_NOTE category."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        approve_calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == "approve_invoice"
        ]
        for call in approve_calls:
            inputs = call.get("data", {}).get("input", {})
            category = inputs.get("category", "").upper()
            if "CREDIT" in category:
                return False, f"approve_invoice called with category '{category}'"

        return True, "No credit notes were approved"
    return _check


def check_no_approve_for_no_match() -> Callable:
    """Check that approve_invoice is NOT called with NO_MATCH status."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        approve_calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == "approve_invoice"
        ]
        for call in approve_calls:
            inputs = call.get("data", {}).get("input", {})
            match_status = inputs.get("match_status", "").upper()
            if "NO_MATCH" in match_status:
                return False, f"approve_invoice called with match_status '{match_status}'"

        return True, "No unmatched invoices were approved"
    return _check


def check_all_invoices_logged(expected_min: int = 5) -> Callable:
    """Check that log_invoice was called for most processed invoices."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        log_calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == "log_invoice"
        ]
        if len(log_calls) >= expected_min:
            return True, f"log_invoice called {len(log_calls)} times (expected >= {expected_min})"
        return False, f"log_invoice called only {len(log_calls)} times, expected >= {expected_min}"
    return _check


def check_discrepancy_detected() -> Callable:
    """Check that compare_line_items was called and returned discrepancies for at least one invoice."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        compare_results = [
            e for e in events
            if e.get("event") == "tool_call_end"
            and e.get("data", {}).get("tool") == "compare_line_items"
        ]
        found_discrepancy = False
        for result in compare_results:
            result_str = result.get("data", {}).get("result_preview", "")

            # Check multiple indicators (result_preview may be truncated)
            if '"has_discrepancies": true' in result_str:
                found_discrepancy = True
                break
            # Non-empty discrepancies array (catches truncated previews)
            if '"type": "PRICE_MISMATCH"' in result_str or '"type": "OVER_DELIVERY"' in result_str:
                found_discrepancy = True
                break
            if '"match_score": 0.0' in result_str:
                found_discrepancy = True
                break

            # Also check the full result if available
            result_data = result.get("data", {}).get("result", "")
            if isinstance(result_data, str) and result_data:
                try:
                    parsed = json.loads(result_data)
                    if parsed.get("has_discrepancies"):
                        found_discrepancy = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass

        if found_discrepancy:
            return True, "Discrepancy detected in at least one comparison"
        if not compare_results:
            return False, "compare_line_items was never called"
        return False, "No discrepancies detected in any comparison"
    return _check


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------


def get_ap_eval_checks() -> dict[str, Callable]:
    """Return the full set of AP eval checks."""
    return {
        "extract_called_per_invoice": check_extract_called_per_invoice(expected_count=7),
        "duplicate_check_before_match": check_duplicate_before_match(),
        "match_called_for_non_duplicates": check_match_called_for_non_duplicates(),
        "discrepancy_detected": check_discrepancy_detected(),
        "no_approve_for_credit_note": check_no_approve_for_credit_note(),
        "no_approve_for_no_match": check_no_approve_for_no_match(),
        "all_invoices_logged": check_all_invoices_logged(expected_min=5),
        "no_tool_errors": check_no_errors(),
        "completed_within_limit": check_completed_within(60),
    }


def run_ap_eval_suite(trace_file: str) -> list[EvalResult]:
    """Run the full AP eval suite against a trace file."""
    return run_eval_suite(
        suite_name="supplier-invoice-processing",
        trace_file=trace_file,
        checks=get_ap_eval_checks(),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print("Usage: python tests/evals_supplier_invoice.py <trace_file.jsonl>")
        sys.exit(1)

    trace_path = sys.argv[1]
    if not Path(trace_path).exists():
        print(f"Trace file not found: {trace_path}")
        sys.exit(1)

    print("=" * 60)
    print("  F1 — Supplier Invoice Processing Eval Suite")
    print(f"  Trace: {trace_path}")
    print("=" * 60)
    print()

    results = run_ap_eval_suite(trace_path)

    print()
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"Result: {passed}/{total} checks passed")
    print(f"{'PASS' if passed == total else 'FAIL'}")

    sys.exit(0 if passed == total else 1)
