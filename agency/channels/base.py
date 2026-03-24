"""Channel interface — the contract every messaging channel must implement.

Every channel (Telegram, Slack, WhatsApp, Teams, Console) implements this.
The agent and HITL hook only talk to this interface — they never know
which platform is underneath.

Key design: send_buttons() BLOCKS until the user taps a button.
This is what makes HITL work — the agent proposes an action, the hook
calls send_buttons() with Approve/Reject, and waits.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable


@dataclass
class IncomingMessage:
    """Normalized incoming message — same format from any channel."""
    text: str
    sender_name: str
    sender_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict | None = None


class Channel(ABC):
    """Base class for messaging channels.

    Every channel must implement:
    - send_message()  — send text to the current conversation
    - send_buttons()  — send text + buttons, BLOCK until user taps one
    - start()         — start listening, call on_message for each incoming msg
    - stop()          — stop listening
    - typing()        — context manager that shows "typing..." indicator
    """

    @abstractmethod
    async def send_message(self, text: str) -> None:
        """Send a text message to the active conversation."""
        ...

    @abstractmethod
    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        """Send message with buttons and wait for user to tap one.

        Args:
            text: The message text (e.g., "Here's the draft email. Send it?")
            buttons: List of dicts with "text" (display) and "value" (returned)
                     e.g., [{"text": "✅ Approve", "value": "approve"},
                            {"text": "✏️ Edit", "value": "edit"},
                            {"text": "❌ Reject", "value": "reject"}]

        Returns:
            The "value" of the button the user clicked.

        This method BLOCKS until the user responds. This is intentional —
        it's how HITL approval works.
        """
        ...

    @abstractmethod
    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        """Start listening for messages. Call on_message for each incoming message."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop listening."""
        ...

    @asynccontextmanager
    async def typing(self):
        """Show typing indicator while the agent is thinking.

        Usage:
            async with channel.typing():
                result = await run_agent(...)

        Default: no-op. Override in subclasses that support typing indicators.
        """
        yield
