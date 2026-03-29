"""WhatsApp channel — Meta Cloud API via pywa.

Setup:
1. Create a Meta Developer account at developers.facebook.com (use business email)
2. Create an app > WhatsApp > get a test phone number
3. Set these in .env:
   WHATSAPP_PHONE_ID       — your phone number ID from Meta dashboard
   WHATSAPP_ACCESS_TOKEN   — permanent token (System User token for prod)
   WHATSAPP_VERIFY_TOKEN   — any string you choose (for webhook verification)
   WHATSAPP_APP_SECRET     — app secret from Meta dashboard (validates webhooks)
   WHATSAPP_ALLOWED_NUMBERS — comma-separated phone numbers allowed to chat (optional)
4. For dev: run with ngrok, point Meta webhook to https://<ngrok>/whatsapp/webhook
5. For prod: deploy behind HTTPS, same webhook URL

WhatsApp-specific constraints:
- Max 3 interactive buttons per message (we enforce this)
- 24-hour messaging window: you can freely reply within 24h of last user message
  After that, you need a pre-approved template message to initiate
- No persistent typing indicator (we send a brief "read" receipt instead)

Usage:
    from agency.channels.whatsapp import WhatsAppChannel
    channel = WhatsAppChannel()
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Awaitable

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)

# WhatsApp allows max 3 interactive buttons
MAX_BUTTONS = 3


class WhatsAppChannel(Channel):
    """WhatsApp messaging channel via Meta Cloud API (pywa).

    Uses pywa_async for async support. Runs a FastAPI server
    internally to receive webhook callbacks from Meta.

    Usage:
        channel = WhatsAppChannel(
            phone_id="123456",
            access_token="EAAG...",
            verify_token="my-secret",
            allowed_numbers=["393331234567"],
        )
    """

    def __init__(
        self,
        phone_id: str | None = None,
        access_token: str | None = None,
        verify_token: str | None = None,
        app_id: str | None = None,
        app_secret: str | None = None,
        allowed_numbers: list[str] | None = None,
        webhook_port: int = 8080,
    ):
        self._phone_id = phone_id or os.environ.get("WHATSAPP_PHONE_ID", "")
        self._access_token = access_token or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
        self._verify_token = verify_token or os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        self._app_id = app_id or os.environ.get("WHATSAPP_APP_ID", "")
        self._app_secret = app_secret or os.environ.get("WHATSAPP_APP_SECRET", "")
        self._webhook_port = int(os.environ.get("WHATSAPP_WEBHOOK_PORT", str(webhook_port)))

        if not self._phone_id or not self._access_token:
            raise ValueError(
                "WhatsApp credentials required. "
                "Set WHATSAPP_PHONE_ID and WHATSAPP_ACCESS_TOKEN."
            )

        # Parse allowed numbers from env (comma-separated) or param
        if allowed_numbers is not None:
            self._allowed_numbers = set(allowed_numbers)
        else:
            raw = os.environ.get("WHATSAPP_ALLOWED_NUMBERS", "")
            self._allowed_numbers = {n.strip() for n in raw.split(",") if n.strip()}

        self._wa = None  # pywa WhatsApp client
        self._fastapi_app = None
        self._server = None  # uvicorn server
        self._on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None

        # Track active conversation for send_message/send_buttons
        self._active_number: str | None = None

        # Pending button responses: {callback_prefix: asyncio.Future}
        self._pending_buttons: dict[str, asyncio.Future] = {}
        self._callback_counter = 0

    async def send_message(self, text: str) -> None:
        if not self._wa or not self._active_number:
            logger.warning("[WhatsApp] No client or active number")
            return

        # WhatsApp has a 4096 char limit per message
        chunks = _split_text(text, max_length=4000)
        for chunk in chunks:
            await self._wa.send_message(to=self._active_number, text=chunk)

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        """Send interactive buttons. WhatsApp supports max 3 buttons."""
        if not self._wa or not self._active_number:
            raise RuntimeError("WhatsApp not connected")

        if len(buttons) > MAX_BUTTONS:
            logger.warning(
                f"[WhatsApp] {len(buttons)} buttons requested, truncating to {MAX_BUTTONS}"
            )
            buttons = buttons[:MAX_BUTTONS]

        from pywa_async.types import Button

        self._callback_counter += 1
        prefix = f"btn_{self._callback_counter}"

        wa_buttons = [
            Button(
                title=btn["text"][:20],  # WhatsApp button title max 20 chars
                callback_data=f"{prefix}:{btn['value']}",
            )
            for btn in buttons
        ]

        await self._wa.send_message(
            to=self._active_number,
            text=text[:4096],  # WhatsApp body limit
            buttons=wa_buttons,
        )

        # Block until user taps a button
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_buttons[prefix] = future

        try:
            result = await asyncio.wait_for(future, timeout=3600)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[WhatsApp] Button response timed out: {prefix}")
            return "timeout"
        finally:
            self._pending_buttons.pop(prefix, None)

    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        """Start the WhatsApp webhook server and register handlers."""
        self._on_message = on_message

        from pywa_async import WhatsApp as PyWaWhatsApp
        from pywa_async import filters as wa_filters
        from pywa_async.types import Message, CallbackButton
        from fastapi import FastAPI
        import uvicorn

        self._fastapi_app = FastAPI(title="WhatsApp Webhook")

        # Callback URL will be set to <your_ngrok>/whatsapp/webhook
        # For dev, Meta sends to this; for prod, your domain
        callback_url = os.environ.get("WHATSAPP_CALLBACK_URL", "")

        wa_kwargs = dict(
            phone_id=self._phone_id,
            token=self._access_token,
            server=self._fastapi_app,
            verify_token=self._verify_token,
        )
        if callback_url:
            wa_kwargs["callback_url"] = callback_url
        if self._app_id:
            wa_kwargs["app_id"] = int(self._app_id)
        if self._app_secret:
            wa_kwargs["app_secret"] = self._app_secret

        self._wa = PyWaWhatsApp(**wa_kwargs)

        # --- Message handler ---
        @self._wa.on_message()
        async def handle_message(_client: PyWaWhatsApp, msg: Message):
            sender = msg.from_user.wa_id  # phone number
            sender_name = msg.from_user.name or sender

            if self._allowed_numbers and sender not in self._allowed_numbers:
                logger.warning(f"[WhatsApp] Ignoring unauthorized number: {sender}")
                return

            self._active_number = sender

            # Mark as read
            try:
                await msg.mark_as_read()
            except Exception:
                pass

            incoming = IncomingMessage(
                text=msg.text or "",
                sender_name=sender_name,
                sender_id=sender,
                raw={"wa_id": sender, "name": sender_name},
            )

            if self._on_message:
                await self._on_message(incoming)

        # --- Button callback handler ---
        @self._wa.on_callback_button()
        async def handle_callback(_client: PyWaWhatsApp, clb: CallbackButton):
            data = clb.data or ""
            parts = data.split(":", 1)
            if len(parts) != 2:
                return

            prefix, value = parts
            future = self._pending_buttons.get(prefix)

            if future and not future.done():
                future.set_result(value)
                # Acknowledge the selection
                try:
                    await clb.reply_text(f"Selected: {value}")
                except Exception:
                    pass

        # --- Start uvicorn in background ---
        config = uvicorn.Config(
            self._fastapi_app,
            host="0.0.0.0",
            port=self._webhook_port,
            log_level="info",
        )
        self._server = uvicorn.Server(config)

        # Run server in a background task so it doesn't block
        asyncio.create_task(self._server.serve())
        logger.info(
            f"[WhatsApp] Webhook server started on port {self._webhook_port}. "
            f"Point Meta webhook to <your_url>/whatsapp/webhook"
        )

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
            logger.info("[WhatsApp] Webhook server stopped")

    @asynccontextmanager
    async def typing(self):
        """WhatsApp doesn't have a persistent typing indicator.

        We mark the chat as 'read' which shows blue ticks — the closest
        equivalent to "I'm looking at this". A future improvement could
        send a brief "Thinking..." message and delete it, but WhatsApp
        doesn't support message deletion by bots easily.
        """
        yield


def _split_text(text: str, max_length: int = 4000) -> list[str]:
    """Split long text into chunks, preferring newline/space breaks."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    return chunks
