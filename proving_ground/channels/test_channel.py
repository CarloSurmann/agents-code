"""Test Channel — Programmable approval channel for automated testing.

Implements the Channel ABC from agency/channels/base.py. Instead of
blocking on real user input, it returns programmed responses instantly.
The entire HITL chain works unchanged — ConfidenceGate writes metadata,
ChannelHITL calls send_buttons() on TestChannel, TestChannel returns the
programmed answer, FeedbackCapture records it.

Supports:
- Default action for all tool calls (auto_approve, auto_skip, etc.)
- Per-tool overrides (e.g., approve send_email but skip send_report)
- Mid-run override injection (change behavior for the next N calls)
- Decision log for eval assertions
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """A recorded HITL decision from the test channel."""
    timestamp: str
    message_text: str
    buttons: list[dict]
    action: str  # The value returned (approve, edit, skip)
    source: str  # "default", "per_tool", "override"


class TestChannel(Channel):
    """Programmable channel that auto-responds to HITL prompts.

    Usage:
        # Auto-approve everything
        channel = TestChannel(default_action="approve")

        # Auto-approve emails, skip everything else
        channel = TestChannel(
            default_action="skip",
            per_tool_actions={"send_email": "approve", "send_support_reply": "approve"},
        )

        # Inject a one-time override mid-run
        channel.inject_override("skip")  # Next call returns "skip" regardless
    """

    def __init__(
        self,
        default_action: str = "approve",
        per_tool_actions: dict[str, str] | None = None,
    ):
        self._default_action = default_action
        self._per_tool_actions = per_tool_actions or {}
        self._overrides: list[str] = []
        self._decisions: list[Decision] = []
        self._messages: list[str] = []

    # ----- Override injection (for mid-run behavior changes) -----

    def inject_override(self, action: str, count: int = 1) -> None:
        """Override the next N responses with a specific action.

        Args:
            action: The action to return (approve, edit, skip).
            count: Number of calls to override.
        """
        self._overrides.extend([action] * count)

    # ----- Test helpers -----

    def get_decisions(self) -> list[Decision]:
        """Get all recorded HITL decisions for eval assertions."""
        return list(self._decisions)

    def get_messages(self) -> list[str]:
        """Get all messages sent through the channel."""
        return list(self._messages)

    def reset(self) -> None:
        """Clear all recorded state."""
        self._decisions.clear()
        self._messages.clear()
        self._overrides.clear()

    # ----- Channel ABC implementation -----

    async def send_message(self, text: str) -> None:
        self._messages.append(text)
        logger.debug(f"[TestChannel] Message: {text[:100]}...")

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        # Determine action
        if self._overrides:
            action = self._overrides.pop(0)
            source = "override"
        else:
            # Try to extract tool name from the message text
            tool_name = self._extract_tool_name(text)
            if tool_name and tool_name in self._per_tool_actions:
                action = self._per_tool_actions[tool_name]
                source = "per_tool"
            else:
                action = self._default_action
                source = "default"

        # Validate the action is one of the available buttons
        valid_values = [b.get("value", "") for b in buttons]
        if action not in valid_values and valid_values:
            action = valid_values[0]
            source = f"fallback({source})"

        decision = Decision(
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_text=text[:500],
            buttons=buttons,
            action=action,
            source=source,
        )
        self._decisions.append(decision)

        logger.info(f"[TestChannel] HITL decision: {action} (source: {source})")
        return action

    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        pass

    async def stop(self) -> None:
        pass

    @asynccontextmanager
    async def typing(self):
        yield

    # ----- Internal helpers -----

    @staticmethod
    def _extract_tool_name(text: str) -> str | None:
        """Try to extract the tool name from HITL message text.

        The ChannelHITL hook typically formats messages like:
        "Tool: send_email\n..." or includes the tool name in the text.
        """
        for line in text.split("\n"):
            line = line.strip()
            if line.lower().startswith("tool:"):
                return line.split(":", 1)[1].strip()
            if line.lower().startswith("**tool:**"):
                return line.split("**", 2)[-1].strip().rstrip("*").strip()
        return None
