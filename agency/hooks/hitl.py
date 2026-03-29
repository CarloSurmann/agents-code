"""Human-in-the-Loop hook — channel-agnostic.

Uses whatever messaging channel is configured (Telegram, WhatsApp, Slack, Console)
to gate tool execution behind human approval. When the agent wants to
call a gated tool (e.g. send_email), it:

1. Sends a formatted summary to the human via the channel
2. Shows Approve / Edit / Skip buttons
3. Waits for the human to tap a button
4. Returns True (proceed) or False (block)

Usage:
    from agency.channels.telegram import TelegramChannel
    from agency.hooks.hitl import ChannelHITL

    channel = TelegramChannel(token="...")
    hitl = ChannelHITL(channel=channel, gated_tools=["send_email"])
    agent = Agent(tools=[send_email], hooks=[hitl])
"""

from __future__ import annotations

import asyncio
import json
import logging

from agency.agent import Hook, ToolCall
from agency.channels.base import Channel

logger = logging.getLogger(__name__)

# Nice labels for known tools
_TOOL_LABELS = {
    "send_email": "Send Email",
    "send_reply": "Send Reply",
    "search_inbox": "Search Inbox",
    "read_message": "Read Email",
    "get_overdue_invoices": "Fetch Invoices",
    "check_payment_status": "Check Payment",
}

# Standard button sets
_APPROVAL_BUTTONS = [
    {"text": "✅ Send", "value": "approve"},
    {"text": "✏️ Edit", "value": "edit"},
    {"text": "❌ Skip", "value": "skip"},
]


class ChannelHITL(Hook):
    """PreToolUse hook that gates actions behind human approval via any channel.

    Works with TelegramChannel, SlackChannel, WhatsAppChannel, ConsoleChannel —
    anything that implements the Channel interface.
    """

    def __init__(
        self,
        channel: Channel,
        gated_tools: list[str] | None = None,
    ):
        self.channel = channel
        self.gated_tools = gated_tools or []

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        """Gate tool execution. Returns True to proceed, False to block."""

        if tool_call.name not in self.gated_tools:
            return True

        # Confidence gate may have set skip_hitl (auto-promoted category)
        if tool_call.metadata.get("skip_hitl"):
            logger.info(f"HITL: {tool_call.name} → auto-approved (confidence gate)")
            return True

        # Add low-confidence warning if flagged
        confidence_prefix = ""
        if tool_call.metadata.get("low_confidence"):
            confidence_prefix = "⚠️ LOW CONFIDENCE — Needs careful review\n\n"

        message = confidence_prefix + self._format_message(tool_call)

        # Store original draft in metadata for feedback capture
        tool_call.metadata["original_draft"] = self._extract_draft(tool_call)

        # Run async send_buttons from sync context
        # (agent loop is sync, channels are async)
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context — run in executor to avoid deadlock
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    asyncio.run,
                    self.channel.send_buttons(message, _APPROVAL_BUTTONS),
                ).result(timeout=3600)
        except RuntimeError:
            # No running loop — just asyncio.run
            result = asyncio.run(
                self.channel.send_buttons(message, _APPROVAL_BUTTONS)
            )

        # Write outcome to metadata for FeedbackCapture hook
        tool_call.metadata["human_action"] = result

        approved = result == "approve"
        status = "approved" if approved else f"rejected ({result})"
        logger.info(f"HITL: {tool_call.name} → {status}")

        return approved

    def _extract_draft(self, tool_call: ToolCall) -> str:
        """Extract the draft text from a tool call for feedback tracking."""
        inputs = tool_call.input
        # Try common field names for email drafts
        return inputs.get("body", inputs.get("draft", inputs.get("text", "")))

    def _format_message(self, tool_call: ToolCall) -> str:
        """Format a human-readable approval request."""
        label = _TOOL_LABELS.get(tool_call.name, tool_call.name)
        inputs = tool_call.input

        if tool_call.name in ("send_email", "send_reply"):
            to = inputs.get("to", "?")
            subject = inputs.get("subject", "?")
            body = inputs.get("body", "")
            if len(body) > 800:
                body = body[:800] + "\n..."

            return (
                f"📧 {label}\n\n"
                f"To: {to}\n"
                f"Subject: {subject}\n\n"
                f"{body}"
            )
        else:
            input_str = json.dumps(inputs, indent=2, ensure_ascii=False)
            if len(input_str) > 1000:
                input_str = input_str[:1000] + "\n..."

            return (
                f"🔔 {label}\n\n"
                f"{input_str}"
            )
