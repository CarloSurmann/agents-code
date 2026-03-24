"""Slack Channel — Channel implementation using slack_bolt.

Based on Carlo's Slack integration, adapted to our Channel interface.
Uses Socket Mode for dev (no public webhook), Events API for prod.

Setup:
    1. Create a Slack app at api.slack.com/apps
    2. Enable Socket Mode, add Bot Token Scopes:
       chat:write, channels:read, groups:read, im:read, im:write, im:history
    3. Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env

Usage:
    from agency.channels.slack import SlackChannel
    channel = SlackChannel()
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable, Awaitable

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class SlackChannel(Channel):
    """Slack messaging channel via slack_bolt + Socket Mode."""

    def __init__(
        self,
        bot_token: str | None = None,
        app_token: str | None = None,
    ):
        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self._bolt_app = None
        self._client = None
        self._handler = None
        self._on_message: Callable | None = None
        self._active_channel: str | None = None

        # Pending button responses
        self._pending_buttons: dict[str, Future] = {}
        self._button_counter = 0

    def _init_bolt(self):
        """Lazy-init the Bolt app + handlers."""
        if self._bolt_app is not None:
            return

        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        self._bolt_app = App(token=self._bot_token)
        self._client = self._bolt_app.client
        self._handler = SocketModeHandler(self._bolt_app, self._app_token)

        # --- DM handler ---
        @self._bolt_app.event("message")
        def handle_message(event, say):
            if event.get("bot_id") or event.get("subtype"):
                return

            self._active_channel = event.get("channel", "")
            text = event.get("text", "").strip()
            if not text:
                return

            msg = IncomingMessage(
                text=text,
                sender_name=event.get("user", "unknown"),
                sender_id=event.get("user", ""),
                timestamp=datetime.now(),
            )

            if self._on_message:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._on_message(msg))
                finally:
                    loop.close()

        # --- @mention handler ---
        @self._bolt_app.event("app_mention")
        def handle_mention(event, say):
            self._active_channel = event.get("channel", "")
            text = re.sub(r"<@\w+>\s*", "", event.get("text", "")).strip()
            if not text:
                say(text="Hi! Send me a message and I'll help you out.")
                return

            msg = IncomingMessage(
                text=text,
                sender_name=event.get("user", "unknown"),
                sender_id=event.get("user", ""),
                timestamp=datetime.now(),
            )

            if self._on_message:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._on_message(msg))
                finally:
                    loop.close()

        # --- Button click handler ---
        @self._bolt_app.action({"type": "button"})
        def handle_button(ack, body):
            ack()
            action = body["actions"][0]
            block_id = action.get("block_id", "")
            value = action.get("value", "")

            future = self._pending_buttons.pop(block_id, None)
            if future and not future.done():
                future.set_result(value)

            # Update message to show selection
            try:
                self._client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    text=f"{body['message']['text']}\n\n_→ Selected: {action.get('text', {}).get('text', value)}_",
                    blocks=[],
                )
            except Exception as e:
                logger.warning(f"Failed to update button message: {e}")

    async def send_message(self, text: str) -> None:
        self._init_bolt()
        if not self._client or not self._active_channel:
            logger.warning("[Slack] No active channel")
            return

        if len(text) > 3000:
            chunks = [text[i:i + 3000] for i in range(0, len(text), 3000)]
            for chunk in chunks:
                self._client.chat_postMessage(channel=self._active_channel, text=chunk)
        else:
            self._client.chat_postMessage(channel=self._active_channel, text=text)

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        self._init_bolt()
        if not self._client or not self._active_channel:
            raise RuntimeError("Slack not connected")

        self._button_counter += 1
        block_id = f"btn_{self._button_counter}"

        elements = []
        for btn in buttons:
            element = {
                "type": "button",
                "text": {"type": "plain_text", "text": btn["text"]},
                "value": btn["value"],
                "action_id": f"btn_action_{self._button_counter}_{btn['value']}",
            }
            if btn["value"] == "approve":
                element["style"] = "primary"
            elif btn["value"] in ("reject", "stop"):
                element["style"] = "danger"
            elements.append(element)

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "block_id": block_id, "elements": elements},
        ]

        self._client.chat_postMessage(
            channel=self._active_channel,
            text=text,
            blocks=blocks,
        )

        future: Future[str] = Future()
        self._pending_buttons[block_id] = future

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, future.result, 3600)
        return result

    async def start(self, on_message: Callable) -> None:
        self._on_message = on_message
        self._init_bolt()

        logger.info("[Slack] Starting Socket Mode")
        thread = threading.Thread(target=self._handler.start, daemon=True)
        thread.start()
        logger.info("[Slack] Bot started")

    async def stop(self) -> None:
        if self._handler:
            self._handler.close()

    @asynccontextmanager
    async def typing(self):
        """Slack doesn't have a persistent typing indicator.
        We could post a 'Thinking...' message and delete it, but for now just yield."""
        yield
