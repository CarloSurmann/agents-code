"""
Conversation Store — Per-user windowed message history.

Inspired by OpenClaw's session routing: each Slack user gets an isolated
conversation context (like agent:main:slack:direct:user123).

Keeps the last N messages per user to provide context to the agent
without re-sending the entire history (which would be expensive).
"""

import threading
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: str       # "user" or "assistant"
    content: str


class ConversationStore:
    """Thread-safe per-user conversation history with windowing."""

    def __init__(self, max_messages: int = 10):
        self._store: dict[str, list[Message]] = {}
        self._lock = threading.Lock()
        self.max_messages = max_messages

    def add(self, user_id: str, role: str, content: str):
        """Add a message to the user's history."""
        with self._lock:
            if user_id not in self._store:
                self._store[user_id] = []
            self._store[user_id].append(Message(role=role, content=content))
            # Window: keep only last N
            self._store[user_id] = self._store[user_id][-self.max_messages:]

    def get_history(self, user_id: str) -> list[Message]:
        """Get the user's conversation history."""
        with self._lock:
            return list(self._store.get(user_id, []))

    def build_context_prompt(self, user_id: str, new_message: str) -> str:
        """
        Build a prompt that includes recent conversation context.

        If no history, just returns the new message.
        If history exists, prepends a summary of recent exchanges.
        """
        history = self.get_history(user_id)
        if not history:
            return new_message

        # Include last 6 messages for context (3 exchanges)
        recent = history[-6:]
        context_lines = []
        for msg in recent:
            speaker = "User" if msg.role == "user" else "You (assistant)"
            context_lines.append(f"{speaker}: {msg.content}")

        context = "\n".join(context_lines)
        return (
            f"## Recent conversation with this user:\n{context}\n\n"
            f"## New message from user:\n{new_message}"
        )

    def clear(self, user_id: str):
        """Clear a user's history."""
        with self._lock:
            self._store.pop(user_id, None)
