"""
Classifier — Two-layer email classification system.

Layer 1: Deterministic rules (fast, free, no LLM)
  Catches the obvious cases: internal emails, auto-replies, short acknowledgments.
  This filters out ~60-70% of emails without spending API tokens.

Layer 2: LLM classification (Claude Sonnet, dedicated call)
  For the ambiguous emails that pass the rules filter.
  Uses a SEPARATE focused API call — not the main agent loop.
  This gives Claude one job and one job only: classify THIS email.

Design insight: The shared-engine doc says "Use rules for what's predictable,
Claude for what's not." This is exactly that.
"""

import json
import re
import logging
from dataclasses import dataclass
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    should_track: bool
    reason: str
    item_type: str = "general"
    context_summary: str = ""
    suggested_schedule: str = "standard"
    classified_by: str = "rules"  # "rules" or "llm"


# ─── LAYER 1: Deterministic Rules ─────────────────────────────────────

# Short acknowledgment patterns — these NEVER need follow-up
ACK_PATTERNS = [
    r"^thanks[.!]?$",
    r"^thank you[.!]?$",
    r"^got it[.!]?$",
    r"^sounds good[.!]?$",
    r"^perfect[.!]?$",
    r"^great[.!]?$",
    r"^ok[.!]?$",
    r"^okay[.!]?$",
    r"^will do[.!]?$",
    r"^noted[.!]?$",
    r"^ack[.!]?$",
    r"^received[.!]?$",
    r"^confirmed[.!]?$",
    r"^👍$",
]
ACK_RE = re.compile("|".join(ACK_PATTERNS), re.IGNORECASE)

# Auto-reply indicators in subject or body
AUTO_REPLY_INDICATORS = [
    "out of office",
    "automatic reply",
    "auto-reply",
    "autoreply",
    "vacation reply",
    "on leave until",
    "delivery status notification",
    "undeliverable",
    "mailer-daemon",
    "noreply",
    "no-reply",
    "do-not-reply",
]

# Calendar/notification patterns in subject
NOISE_SUBJECT_PATTERNS = [
    r"^(accepted|declined|tentative):",
    r"^(invitation|updated invitation):",
    r"calendar:",
    r"^re: (accepted|declined|tentative):",
    r"shared .* with you$",
    r"commented on",
    r"assigned you",
    r"notification",
]
NOISE_SUBJECT_RE = re.compile("|".join(NOISE_SUBJECT_PATTERNS), re.IGNORECASE)


def apply_rules(
    subject: str,
    body: str,
    to_email: str,
    from_email: str,
    has_attachments: bool,
) -> ClassificationResult | None:
    """
    Apply deterministic rules. Returns a result if a clear decision can be made,
    or None if the email should be passed to the LLM.
    """

    body_stripped = body.strip()
    body_first_line = body_stripped.split("\n")[0].strip() if body_stripped else ""
    body_lower = body_stripped.lower()
    subject_lower = subject.lower()

    # Rule 1: Internal email (same domain)
    from_domain = from_email.split("@")[-1].lower() if "@" in from_email else ""
    to_domain = to_email.split("@")[-1].lower() if "@" in to_email else ""
    if from_domain and to_domain and from_domain == to_domain:
        return ClassificationResult(
            should_track=False,
            reason=f"Internal email (both @{from_domain})",
            classified_by="rules",
        )

    # Rule 2: Auto-reply / bounce
    for indicator in AUTO_REPLY_INDICATORS:
        if indicator in subject_lower or indicator in body_lower:
            return ClassificationResult(
                should_track=False,
                reason=f"Auto-reply detected: '{indicator}'",
                classified_by="rules",
            )

    # Rule 3: Calendar/notification noise
    if NOISE_SUBJECT_RE.search(subject):
        return ClassificationResult(
            should_track=False,
            reason=f"Calendar or notification email: '{subject}'",
            classified_by="rules",
        )

    # Rule 4: Very short body = acknowledgment
    # Check both the first line against strict patterns AND the full short body
    # against looser matching for compound acks like "Got it, sounds good"
    is_short_ack = False
    if len(body_stripped) < 80:
        if ACK_RE.match(body_first_line):
            is_short_ack = True
        else:
            # Check if ALL words/phrases in the body are acknowledgment-like
            ack_words = {"thanks", "thank you", "got it", "sounds good", "perfect",
                         "great", "ok", "okay", "will do", "noted", "received",
                         "confirmed", "cheers", "much appreciated", "all good",
                         "no worries", "understood", "roger", "cool", "nice"}
            # Strip punctuation and split by common separators
            cleaned = re.sub(r"[.!,;:\-\n]+", " ", body_lower).strip()
            words_in_body = [w.strip() for w in re.split(r"\s{2,}", cleaned) if w.strip()]
            # Also try the full cleaned string and individual comma-separated parts
            parts = [p.strip() for p in re.sub(r"[.!;\n]+", ",", body_lower).split(",") if p.strip()]
            if all(p in ack_words for p in parts) and len(parts) <= 4:
                is_short_ack = True

    if is_short_ack:
        return ClassificationResult(
            should_track=False,
            reason=f"Short acknowledgment: '{body_first_line}'",
            classified_by="rules",
        )

    # Rule 5: Empty body with no attachments
    if len(body_stripped) < 10 and not has_attachments:
        return ClassificationResult(
            should_track=False,
            reason="Empty or near-empty email with no attachments",
            classified_by="rules",
        )

    # No clear rule matched → pass to LLM
    return None


# ─── LAYER 2: LLM Classification ──────────────────────────────────────

CLASSIFY_SYSTEM_PROMPT = """You are a precise email classifier. Your ONLY job is to determine if a sent email needs follow-up tracking.

An email needs tracking when:
- It asks a question and expects a response
- It sends a proposal, quote, estimate, or pricing
- It requests a meeting, call, or specific action
- It sends a document for review or approval
- It sends an invoice or payment request

An email does NOT need tracking when:
- It's a simple reply ("thanks", "got it", "sounds good")
- It's sharing information with no expectation of response
- It's a routine update or status report
- It's a farewell, congratulations, or social email
- It's forwarding something FYI-only

You MUST respond with ONLY valid JSON. No markdown, no explanation, no code blocks. Just the raw JSON object."""

CLASSIFY_USER_TEMPLATE = """Classify this sent email:

TO: {to_name} <{to_email}>
SUBJECT: {subject}
ATTACHMENTS: {attachments}
BODY:
---
{body}
---

Respond with JSON:
{{"should_track": true/false, "reason": "one sentence", "item_type": "proposal|quote|invoice|request|review|question|general", "context_summary": "1-3 sentences for follow-up context", "suggested_schedule": "standard|urgent|relaxed"}}"""


def classify_with_llm(
    subject: str,
    body: str,
    to_email: str,
    to_name: str,
    has_attachments: bool,
    attachment_names: list[str] | None = None,
    model: str = "claude-sonnet-4-20250514",
    api_key: str | None = None,
) -> ClassificationResult:
    """
    Classify an email using a DEDICATED Claude call.

    This is NOT part of the main agent loop. It's a standalone, focused API call
    where Claude has one job: classify this email. No tools, no distractions.
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    attachments_str = ", ".join(attachment_names) if attachment_names else "None"

    # Truncate body to avoid wasting tokens on very long emails
    body_for_classification = body[:1500] if len(body) > 1500 else body

    user_message = CLASSIFY_USER_TEMPLATE.format(
        to_name=to_name or "Unknown",
        to_email=to_email,
        subject=subject,
        attachments=attachments_str,
        body=body_for_classification,
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        raw_text = response.content[0].text.strip()

        # Clean up common LLM output issues
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`").strip()
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        result = json.loads(raw_text)

        return ClassificationResult(
            should_track=result.get("should_track", False),
            reason=result.get("reason", "No reason given"),
            item_type=result.get("item_type", "general"),
            context_summary=result.get("context_summary", ""),
            suggested_schedule=result.get("suggested_schedule", "standard"),
            classified_by="llm",
        )

    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON: {raw_text[:200]}... Error: {e}")
        # Default to tracking — better to track too much than miss something
        return ClassificationResult(
            should_track=True,
            reason=f"Classification failed (JSON parse error), defaulting to track",
            item_type="general",
            context_summary=f"Email to {to_email}: {subject}",
            classified_by="llm_fallback",
        )

    except Exception as e:
        logger.error(f"LLM classification failed: {e}")
        return ClassificationResult(
            should_track=True,
            reason=f"Classification failed ({e}), defaulting to track",
            item_type="general",
            context_summary=f"Email to {to_email}: {subject}",
            classified_by="llm_error",
        )


# ─── Combined classifier ──────────────────────────────────────────────

def classify_email(
    subject: str,
    body: str,
    to_email: str,
    to_name: str,
    from_email: str,
    has_attachments: bool = False,
    attachment_names: list[str] | None = None,
    model: str = "claude-sonnet-4-20250514",
) -> ClassificationResult:
    """
    Classify a sent email using the two-layer system.

    Layer 1 (rules) runs first — free and instant.
    Layer 2 (LLM) only runs if rules can't decide — costs ~0.001€ per email.
    """

    # Layer 1: Rules
    rules_result = apply_rules(subject, body, to_email, from_email, has_attachments)
    if rules_result is not None:
        logger.info(f"Rules classified '{subject}': {rules_result.should_track} ({rules_result.reason})")
        return rules_result

    # Layer 2: LLM
    logger.info(f"Rules inconclusive for '{subject}', calling LLM...")
    llm_result = classify_with_llm(
        subject=subject,
        body=body,
        to_email=to_email,
        to_name=to_name,
        has_attachments=has_attachments,
        attachment_names=attachment_names,
        model=model,
    )
    logger.info(f"LLM classified '{subject}': {llm_result.should_track} ({llm_result.reason})")
    return llm_result


# ─── Tool function for the agent ───────────────────────────────────────

def classify_sent_email(
    subject: str,
    body: str,
    to_email: str,
    to_name: str,
    from_email: str,
    has_attachments: bool,
) -> str:
    """Classify a sent email to determine if it needs follow-up tracking. Uses deterministic rules first, then LLM for ambiguous cases. Returns classification with should_track, reason, item_type, context_summary."""
    result = classify_email(
        subject=subject,
        body=body,
        to_email=to_email,
        to_name=to_name,
        from_email=from_email,
        has_attachments=has_attachments,
    )
    return json.dumps({
        "should_track": result.should_track,
        "reason": result.reason,
        "item_type": result.item_type,
        "context_summary": result.context_summary,
        "suggested_schedule": result.suggested_schedule,
        "classified_by": result.classified_by,
    })
