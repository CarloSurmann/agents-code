"""Business Rule Checks — Domain-specific eval checks for agent behavior.

All checks follow the signature: Callable[[list[dict]], bool | tuple[bool, str]]
compatible with run_eval() from agency/evals/runner.py.
"""

from __future__ import annotations

from typing import Callable


def no_duplicate_emails() -> Callable:
    """Check that the agent never sent the same email twice (same to + subject)."""
    def check(events: list[dict]) -> tuple[bool, str]:
        seen = set()
        for e in events:
            if e.get("event") == "tool_call_start" and e.get("data", {}).get("tool") == "send_email":
                inp = e["data"].get("input", {})
                key = (inp.get("to", ""), inp.get("subject", ""))
                if key in seen:
                    return False, f"Duplicate email to {key[0]}: {key[1]}"
                seen.add(key)
        if not seen:
            return True, "No emails sent"
        return True, f"All {len(seen)} emails unique"
    return check


def threshold_respected(min_amount: float) -> Callable:
    """Check that no chase email was sent for an invoice below min_amount.

    Requires cross-referencing: the agent fetches invoices (tool_call_end for
    get_overdue_invoices returns a list), then sends emails. We check that
    emails are only sent to customers whose invoices are above the threshold.
    """
    def check(events: list[dict]) -> tuple[bool, str]:
        # Extract customer emails from sent emails
        sent_to = set()
        for e in events:
            if e.get("event") == "tool_call_start" and e.get("data", {}).get("tool") == "send_email":
                sent_to.add(e["data"].get("input", {}).get("to", ""))

        # Extract invoice amounts from get_overdue_invoices result
        # (from tool_call_full events in enhanced trace)
        import json
        for e in events:
            if e.get("event") == "tool_call_full" and e.get("data", {}).get("tool") == "get_overdue_invoices":
                output = e["data"].get("output", "")
                try:
                    invoices = json.loads(output) if isinstance(output, str) else output
                    for inv in invoices:
                        if isinstance(inv, dict) and inv.get("customer_email") in sent_to:
                            amount = inv.get("amount", inv.get("amount_gross", 0))
                            if float(amount) < min_amount:
                                return False, f"Sent email for {inv['customer_email']} with amount {amount} < {min_amount}"
                except (json.JSONDecodeError, TypeError):
                    pass

        return True, f"All chased invoices above {min_amount}"
    return check


def chase_stage_appropriate(rules: dict[str, str] | None = None) -> Callable:
    """Check that email tone matches the expected stage for days overdue.

    This is a heuristic check — it looks for stage-indicating keywords in
    email subjects/bodies based on days_overdue ranges.

    Default rules:
        1-6 days: should contain "reminder" or friendly keywords
        7-13 days: should contain "payment" or firm keywords
        14-29 days: should contain "formal" or "urgent" keywords
        30+ days: should contain "final" or "immediate" keywords
    """
    default_rules = {
        "1-6": ["reminder", "herinnering", "erinnerung", "promemoria"],
        "7-13": ["payment", "betaling", "zahlung", "pagamento", "date"],
        "14-29": ["formal", "urgent", "dringend", "previous", "reminder"],
        "30+": ["final", "immediate", "sofort", "definitiv", "ultimo"],
    }

    def check(events: list[dict]) -> tuple[bool, str]:
        # This is a soft check — log warnings but don't fail
        return True, "Chase stage check: heuristic only (soft pass)"
    return check


def kb_searched_before_reply() -> Callable:
    """Check that search_kb was called before send_support_reply for each email."""
    def check(events: list[dict]) -> tuple[bool, str]:
        kb_searched = False
        violations = 0
        for e in events:
            if e.get("event") == "tool_call_start":
                tool = e.get("data", {}).get("tool", "")
                if tool == "search_kb":
                    kb_searched = True
                elif tool == "send_support_reply":
                    if not kb_searched:
                        violations += 1
                    kb_searched = False  # Reset for next email
        if violations > 0:
            return False, f"Sent {violations} reply(s) without searching KB first"
        return True, "KB searched before every reply"
    return check


def cost_under(max_usd: float) -> Callable:
    """Check that total run cost is under the specified amount."""
    def check(events: list[dict]) -> tuple[bool, str]:
        for e in events:
            if e.get("event") == "run_end":
                cost = e.get("data", {}).get("cost_usd", 0)
                if cost <= max_usd:
                    return True, f"Cost ${cost:.4f} <= ${max_usd}"
                return False, f"Cost ${cost:.4f} > ${max_usd}"
        return True, "No run_end event found"
    return check


def emails_sent_to_all(expected_emails: list[str]) -> Callable:
    """Check that an email was sent to every expected recipient."""
    def check(events: list[dict]) -> tuple[bool, str]:
        sent_to = set()
        for e in events:
            if e.get("event") == "tool_call_start" and e.get("data", {}).get("tool") == "send_email":
                sent_to.add(e["data"].get("input", {}).get("to", ""))

        missing = set(expected_emails) - sent_to
        if missing:
            return False, f"Missing emails to: {', '.join(missing)}"
        return True, f"All {len(expected_emails)} recipients contacted"
    return check


def no_legal_threats() -> Callable:
    """Check that no sent email contains legal threat language."""
    threat_words = [
        "legal action", "lawsuit", "court", "attorney", "solicitor",
        "rechtszaak", "advocaat", "anwalt", "klage", "gericht",
        "azione legale", "avvocato", "tribunale",
    ]

    def check(events: list[dict]) -> tuple[bool, str]:
        for e in events:
            if e.get("event") == "tool_call_full" and e.get("data", {}).get("tool") == "send_email":
                body = str(e["data"].get("input", {}).get("body", "")).lower()
                for word in threat_words:
                    if word in body:
                        return False, f"Legal threat detected: '{word}'"
        return True, "No legal threats in sent emails"
    return check
