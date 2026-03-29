"""
Confidence — Score agent decisions and route based on confidence bands.

Three bands:
  - HIGH (>threshold): auto-execute, skip HITL (after trust is built)
  - MEDIUM (between thresholds): normal HITL with draft
  - LOW (<threshold): flag for human, show low-confidence warning

Scoring sources (in priority order):
  1. Feedback history — auto-promoted category → 0.95
  2. Rules-based classification → 0.92
  3. LLM-based classification → 0.75
  4. Default → 0.70
"""

import logging
from dataclasses import dataclass

from agency import feedback

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceResult:
    """Result of confidence scoring for a tool call."""
    score: float        # 0.0 to 1.0
    band: str           # "high", "medium", "low"
    reasoning: str      # one-line explanation
    source: str         # "feedback_history", "rules", "llm", "default"


def score_decision(
    tool_name: str,
    tool_input: dict,
    client_id: str = "",
    category: str = "",
    high_threshold: float = 0.85,
    low_threshold: float = 0.60,
) -> ConfidenceResult:
    """Score confidence for a pending tool call.

    Checks feedback history first (cheapest, most reliable), then
    falls back to classification signal, then default.
    """

    score = 0.70
    source = "default"
    reasoning = "No prior data — using default confidence"

    # 1. Check if this (client, tool, category) is auto-promoted
    try:
        if category and feedback.check_auto_promote(client_id, tool_name, category):
            streak = feedback.get_streak(client_id, tool_name, category)
            score = 0.95
            source = "feedback_history"
            reasoning = f"Auto-promoted after {streak} consecutive approvals"
    except RuntimeError:
        # Feedback DB not initialized — skip
        pass

    # 2. Check classification signal from tool input
    if source == "default":
        classified_by = tool_input.get("_classified_by", "")
        if classified_by == "rules":
            score = 0.92
            source = "rules"
            reasoning = "Classified by deterministic rules (high reliability)"
        elif classified_by == "llm":
            score = 0.75
            source = "llm"
            reasoning = "Classified by LLM (moderate reliability)"

    # 3. Check approval rate for this category (softer signal)
    if source == "default" and category:
        try:
            rate = feedback.get_approval_rate(client_id, tool_name, days=14)
            if rate > 0.90 and rate > 0:
                score = 0.82
                source = "approval_rate"
                reasoning = f"High recent approval rate ({rate:.0%}) for this tool"
        except RuntimeError:
            pass

    # Determine band
    if score >= high_threshold:
        band = "high"
    elif score >= low_threshold:
        band = "medium"
    else:
        band = "low"

    return ConfidenceResult(score=score, band=band, reasoning=reasoning, source=source)
