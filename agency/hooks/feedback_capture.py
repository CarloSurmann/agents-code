"""FeedbackCapture — PostToolUse hook that records HITL outcomes.

Reads metadata written by ChannelHITL (human_action, original_draft, etc.)
and writes to the feedback database for learning and auto-promotion.

Hook order: [ConfidenceGate, ChannelHITL, FeedbackCapture]
  - ConfidenceGate sets confidence metadata
  - ChannelHITL sets human_action metadata
  - FeedbackCapture reads both and records to DB
"""

import logging
from typing import Any

from agency.agent import Hook, ToolCall
from agency import feedback

logger = logging.getLogger(__name__)


class FeedbackCapture(Hook):
    """PostToolUse hook that records HITL decisions into the feedback DB."""

    def __init__(
        self,
        client_id: str = "",
        agent_name: str = "",
        auto_promote_threshold: int = 20,
    ):
        self.client_id = client_id
        self.agent_name = agent_name
        self.auto_promote_threshold = auto_promote_threshold

    def post_tool_use(self, tool_call: ToolCall, result: Any) -> None:
        """Record the decision if HITL metadata is present."""
        meta = tool_call.metadata

        # Only record if there was a HITL decision (or auto-execute)
        human_action = meta.get("human_action")
        route_taken = meta.get("route_taken", "hitl_with_draft")

        if human_action is None and route_taken != "auto_execute":
            return  # Not a gated tool call, nothing to record

        # For auto-executed calls, treat as "approve"
        if route_taken == "auto_execute":
            human_action = human_action or "auto_approve"

        category = meta.get("category", "")

        # Record the decision
        try:
            feedback.record_decision(
                tool_name=tool_call.name,
                tool_input=tool_call.input,
                human_action=human_action,
                confidence=meta.get("confidence"),
                confidence_band=meta.get("confidence_band", ""),
                route_taken=route_taken,
                original_draft=meta.get("original_draft", ""),
                human_edit_text=meta.get("human_edit_text", ""),
                client_id=self.client_id,
                agent_name=self.agent_name,
                category=category,
            )

            # Update streak
            streak_result = feedback.update_streak(
                client_id=self.client_id,
                tool_name=tool_call.name,
                category=category,
                human_action=human_action,
                auto_promote_threshold=self.auto_promote_threshold,
            )

            if streak_result["promoted"]:
                logger.info(
                    f"🎓 Auto-promotion triggered: {tool_call.name}/{category} "
                    f"for {self.client_id} (streak: {streak_result['streak']})"
                )

        except Exception as e:
            # Never crash the agent loop because of feedback recording
            logger.error(f"Failed to record feedback: {e}")
