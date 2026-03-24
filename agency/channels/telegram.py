"""Telegram channel — bot interface via python-telegram-bot.

Setup:
1. Message @BotFather on Telegram, create a bot, get the token
2. Set TELEGRAM_BOT_TOKEN in your .env
3. Set TELEGRAM_CHAT_ID to the chat where the bot should talk

Supports:
- Text messages with Markdown
- Inline keyboard buttons (for HITL approval)
- Typing indicator while agent thinks
- Long polling (dev) or webhook (prod)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Awaitable

from telegram import (
    Bot,
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class TelegramChannel(Channel):
    """Telegram messaging channel.

    Usage:
        channel = TelegramChannel(
            token="your-bot-token",
            allowed_chat_ids=["123456789"],
        )
    """

    def __init__(
        self,
        token: str | None = None,
        allowed_chat_ids: list[str] | None = None,
    ):
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise ValueError("Telegram bot token required. Set TELEGRAM_BOT_TOKEN.")

        self._allowed_chat_ids = set(allowed_chat_ids or [])
        self._app: Application | None = None
        self._bot: Bot | None = None
        self._on_message: Callable | None = None

        # Track the active chat for send_message/send_buttons
        self._active_chat_id: str | None = None

        # Pending button responses: {prefix: asyncio.Future}
        self._pending_buttons: dict[str, asyncio.Future] = {}
        self._callback_counter = 0

    @property
    def chat_id(self) -> str:
        """The currently active chat ID."""
        if self._active_chat_id:
            return self._active_chat_id
        # Fallback to env var
        return os.environ.get("TELEGRAM_CHAT_ID", "")

    async def send_message(self, text: str) -> None:
        if not self._bot or not self.chat_id:
            logger.warning("[Telegram] No bot or chat_id")
            return

        chunks = _split_text(text, max_length=4000)
        for chunk in chunks:
            try:
                await self._bot.send_message(
                    chat_id=int(self.chat_id),
                    text=chunk,
                    parse_mode="Markdown",
                )
            except Exception:
                # Retry without Markdown if parsing fails
                await self._bot.send_message(
                    chat_id=int(self.chat_id),
                    text=chunk,
                )

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        if not self._bot or not self.chat_id:
            raise RuntimeError("Telegram not connected")

        self._callback_counter += 1
        prefix = f"btn_{self._callback_counter}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                text=btn["text"],
                callback_data=f"{prefix}:{btn['value']}",
            ) for btn in buttons]
        ])

        await self._bot.send_message(
            chat_id=int(self.chat_id),
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

        # Wait for callback
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_buttons[prefix] = future

        try:
            result = await asyncio.wait_for(future, timeout=3600)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Button response timed out for {prefix}")
            return "timeout"
        finally:
            self._pending_buttons.pop(prefix, None)

    async def start(self, on_message: Callable) -> None:
        self._on_message = on_message

        self._app = Application.builder().token(self._token).build()
        self._bot = self._app.bot

        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text_message,
        ))
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started (polling mode)")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    @asynccontextmanager
    async def typing(self):
        """Show 'typing...' indicator while agent is thinking."""
        task = asyncio.create_task(self._typing_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _typing_loop(self):
        try:
            while True:
                if self._bot and self.chat_id:
                    await self._bot.send_chat_action(
                        chat_id=int(self.chat_id),
                        action=ChatAction.TYPING,
                    )
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    # --- Internal handlers ---

    async def _handle_text_message(self, update: Update, context) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = str(update.message.chat_id)

        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            logger.warning(f"Ignoring unauthorized chat: {chat_id}")
            return

        # Track active chat
        self._active_chat_id = chat_id

        msg = IncomingMessage(
            text=update.message.text,
            sender_name=update.message.from_user.first_name or "Unknown",
            sender_id=str(update.message.from_user.id),
            raw=update.to_dict(),
        )

        if self._on_message:
            await self._on_message(msg)

    async def _handle_callback(self, update: Update, context) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        await query.answer()

        parts = query.data.split(":", 1)
        if len(parts) != 2:
            return

        prefix, callback_id = parts
        future = self._pending_buttons.get(prefix)

        if future and not future.done():
            future.set_result(callback_id)
            await query.edit_message_reply_markup(reply_markup=None)
            try:
                await query.edit_message_text(
                    text=f"{query.message.text}\n\n_Selected: {callback_id}_",
                    parse_mode="Markdown",
                )
            except Exception:
                pass


def _split_text(text: str, max_length: int = 4000) -> list[str]:
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
