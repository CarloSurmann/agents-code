"""Console Channel — Terminal-based channel for dev/testing.

No Telegram, no Slack, no WhatsApp. Just stdin/stdout.
Useful for quick testing of agents without setting up any messaging platform.

Usage:
    from agency.channels.console import ConsoleChannel

    channel = ConsoleChannel()
    # In server.py, swap TelegramChannel for ConsoleChannel
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class ConsoleChannel(Channel):
    """Terminal-based channel — reads from stdin, writes to stdout."""

    async def send_message(self, text: str) -> None:
        print(f"\n🤖 Agent: {text}")

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        """Show buttons as numbered options, wait for user to pick."""
        print(f"\n🤖 Agent: {text}")
        print()
        for i, btn in enumerate(buttons, 1):
            print(f"  [{i}] {btn['text']}")
        print()

        while True:
            try:
                choice = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("Your choice (number): ").strip()
                )
                idx = int(choice) - 1
                if 0 <= idx < len(buttons):
                    selected = buttons[idx]
                    print(f"  → {selected['text']}")
                    return selected["value"]
                else:
                    print(f"  Please enter 1-{len(buttons)}")
            except (ValueError, EOFError):
                print(f"  Please enter a number 1-{len(buttons)}")

    async def start(self, on_message: Callable) -> None:
        """Run a REPL loop reading from stdin."""
        print("=" * 50)
        print("  Console Channel — type 'quit' to exit")
        print("=" * 50)

        loop = asyncio.get_event_loop()

        while True:
            try:
                text = await loop.run_in_executor(
                    None, lambda: input("\n👤 You: ").strip()
                )
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if text.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            if not text:
                continue

            msg = IncomingMessage(
                text=text,
                sender_name="dev",
                sender_id="console",
                timestamp=datetime.now(),
            )
            await on_message(msg)

    async def stop(self) -> None:
        pass

    @asynccontextmanager
    async def typing(self):
        """In console, just print a thinking indicator."""
        print("  ⏳ Thinking...", end="", flush=True)
        try:
            yield
        finally:
            print("\r" + " " * 20 + "\r", end="", flush=True)
