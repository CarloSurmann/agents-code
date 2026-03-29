"""ConfidenceGate — PreToolUse hook for confidence-based routing.

Runs BEFORE ChannelHITL in the hooks list. Computes a confidence score
for the pending tool call and sets metadata that HITL reads:

  - skip_hitl=True → HITL returns True immediately (auto-execute)
  - low_confidence=True → HITL shows a warning banner

Hook order: [ConfidenceGate, ChannelHITL, FeedbackCapture]
"""

import asyncio
import logging
from typing import Any

from agency.agent import Hook, ToolCall
from agency.confidence import score_decision, ConfidenceResult
from agency.channels.base import Channel

logger = logging.getLogger(__name__)


class ConfidenceGate(Hook):
    """PreToolUse hook that scores confidence and routes decisions."""

    def __init__(
        self,
        gated_tools: list[str] | None = None,
        client_id: str = "",
        high_threshold: float = 0.85,
        low_threshold: float = 0.60,
        channel: Channel | None = None,
    ):
        self.gated_tools = gated_tools or []
        self.client_id = client_id
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.channel = channel

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        """Score confidence and set metadata. Never blocks — always returns True."""

        # Only score tools that are HITL-gated
        if tool_call.name not in self.gated_tools:
            return True

        # Extract category from tool input (support-specific fields)
        category = (
            tool_call.input.get("category", "")
            or tool_call.input.get("item_type", "")
            or tool_call.input.get("_category", "")
        )
        tool_call.metadata["category"] = category

        # Score
        result = score_decision(
            tool_name=tool_call.name,
            tool_input=tool_call.input,
            client_id=self.client_id,
            category=category,
            high_threshold=self.high_threshold,
            low_threshold=self.low_threshold,
        )

        # Write to metadata for downstream hooks
        tool_call.metadata["confidence"] = result.score
        tool_call.metadata["confidence_band"] = result.band
        tool_call.metadata["confidence_source"] = result.source
        tool_call.metadata["confidence_reasoning"] = result.reasoning

        # Route based on band
        if result.band == "high":
            tool_call.metadata["skip_hitl"] = True
            tool_call.metadata["route_taken"] = "auto_execute"
            tool_call.metadata["human_action"] = "auto_approve"
            logger.info(
                f"✅ Auto-approved {tool_call.name}/{category} "
                f"(confidence: {result.score:.2f}, source: {result.source})"
            )
            # Notify channel
            if self.channel:
                self._notify_auto_approve(tool_call, result)

        elif result.band == "low":
            tool_call.metadata["low_confidence"] = True
            tool_call.metadata["route_taken"] = "hitl_low_confidence"
            logger.info(
                f"⚠️ Low confidence for {tool_call.name}/{category} "
                f"({result.score:.2f}) — flagging for human"
            )
        else:
            tool_call.metadata["route_taken"] = "hitl_with_draft"

        return True  # Never blocks — just sets metadata

    def _notify_auto_approve(self, tool_call: ToolCall, result: ConfidenceResult):
        """Send a notification that an action was auto-approved."""
        category = tool_call.metadata.get("category", "")
        msg = (
            f"✅ Auto-approved: {tool_call.name}"
            f"{f' ({category})' if category else ''}\n"
            f"Confidence: {result.score:.0%} | Source: {result.source}\n"
            f"Reason: {result.reasoning}"
        )
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, self.channel.send_message(msg)).result(timeout=10)
        except RuntimeError:
            asyncio.run(self.channel.send_message(msg))
        except Exception as e:
            logger.warning(f"Failed to send auto-approve notification: {e}")
