"""Microsoft Teams channel — Bot Framework SDK v4.

Setup:
1. Register a bot in Azure Portal → Bot Services → Azure Bot
2. Get the App ID and App Password (client secret)
3. Set these in .env:
   TEAMS_APP_ID         — Azure Bot registration App ID
   TEAMS_APP_PASSWORD   — Azure Bot registration password/secret
   TEAMS_ALLOWED_USERS  — comma-separated Azure AD user IDs (optional)
4. For dev: run with ngrok, set messaging endpoint to https://<ngrok>/api/messages
5. For prod: deploy behind HTTPS, same endpoint

Note on the SDK:
Microsoft is migrating from botbuilder-python to microsoft-agents-* SDK,
but the new SDK is still in Frontier preview (March 2026). We use the
stable botbuilder SDK for now — migration path is straightforward when
the new SDK goes GA.

Usage:
    from agency.channels.teams import TeamsChannel
    channel = TeamsChannel()
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Callable, Awaitable

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    CardFactory,
    MessageFactory,
    TurnContext,
)
from botbuilder.schema import (
    Activity,
    ActivityTypes,
    CardAction,
    HeroCard,
)
from botbuilder.schema._connector_client_enums import ActionTypes
from aiohttp import web

from agency.channels.base import Channel, IncomingMessage

logger = logging.getLogger(__name__)


class TeamsChannel(Channel):
    """Microsoft Teams messaging channel via Bot Framework SDK.

    Runs an aiohttp web server to receive webhook callbacks from
    Azure Bot Service. Messages from Teams users are forwarded to
    the agent; replies are sent back through the same turn context
    or via proactive messaging.

    Usage:
        channel = TeamsChannel(
            app_id="your-azure-app-id",
            app_password="your-azure-app-password",
        )
    """

    def __init__(
        self,
        app_id: str | None = None,
        app_password: str | None = None,
        tenant_id: str | None = None,
        allowed_users: list[str] | None = None,
        webhook_port: int = 3978,
    ):
        self._app_id = app_id or os.environ.get("TEAMS_APP_ID", "")
        self._app_password = app_password or os.environ.get("TEAMS_APP_PASSWORD", "")
        self._tenant_id = tenant_id or os.environ.get("TEAMS_TENANT_ID", "")
        self._webhook_port = int(os.environ.get("TEAMS_WEBHOOK_PORT", str(webhook_port)))

        if not self._app_id or not self._app_password:
            raise ValueError(
                "Teams credentials required. "
                "Set TEAMS_APP_ID and TEAMS_APP_PASSWORD."
            )

        # Parse allowed users from env (comma-separated) or param
        if allowed_users is not None:
            self._allowed_users = set(allowed_users)
        else:
            raw = os.environ.get("TEAMS_ALLOWED_USERS", "")
            self._allowed_users = {u.strip() for u in raw.split(",") if u.strip()}

        settings = BotFrameworkAdapterSettings(
            self._app_id,
            self._app_password,
            channel_auth_tenant=self._tenant_id or None,
        )
        self._adapter = BotFrameworkAdapter(settings)

        self._on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None
        self._web_app: web.Application | None = None
        self._runner: web.AppRunner | None = None

        # Store the latest turn context for proactive messaging
        # (send_message/send_buttons outside of a turn)
        self._conversation_reference: dict | None = None
        self._active_turn_context: TurnContext | None = None

        # Pending button responses: {action_id: asyncio.Future}
        self._pending_buttons: dict[str, asyncio.Future] = {}
        self._callback_counter = 0

    async def send_message(self, text: str) -> None:
        """Send a text message to the active Teams conversation."""
        if self._active_turn_context:
            # We're inside an active turn — reply directly
            chunks = _split_text(text, max_length=4000)
            for chunk in chunks:
                await self._active_turn_context.send_activity(chunk)
            return

        # Outside a turn — use proactive messaging
        if not self._conversation_reference:
            logger.warning("[Teams] No conversation reference for proactive message")
            return

        async def send_callback(turn_context: TurnContext):
            chunks = _split_text(text, max_length=4000)
            for chunk in chunks:
                await turn_context.send_activity(chunk)

        await self._adapter.continue_conversation(
            self._conversation_reference,
            send_callback,
            self._app_id,
        )

    async def send_buttons(self, text: str, buttons: list[dict]) -> str:
        """Send a Hero Card with action buttons. Blocks until user clicks one."""
        self._callback_counter += 1
        prefix = f"btn_{self._callback_counter}"

        card_actions = [
            CardAction(
                type=ActionTypes.message_back,
                title=btn["text"],
                text=f"{prefix}:{btn['value']}",
                display_text=btn["text"],
                value={"action": prefix, "value": btn["value"]},
            )
            for btn in buttons
        ]

        card = HeroCard(text=text, buttons=card_actions)
        message = MessageFactory.attachment(CardFactory.hero_card(card))

        # Send the card
        if self._active_turn_context:
            await self._active_turn_context.send_activity(message)
        elif self._conversation_reference:
            async def send_card(turn_context: TurnContext):
                await turn_context.send_activity(message)

            await self._adapter.continue_conversation(
                self._conversation_reference,
                send_card,
                self._app_id,
            )
        else:
            raise RuntimeError("Teams not connected — no active context or reference")

        # Block until user clicks a button
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending_buttons[prefix] = future

        try:
            result = await asyncio.wait_for(future, timeout=3600)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[Teams] Button response timed out: {prefix}")
            return "timeout"
        finally:
            self._pending_buttons.pop(prefix, None)

    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        """Start the aiohttp webhook server for Bot Framework."""
        self._on_message = on_message

        self._web_app = web.Application()
        self._web_app.router.add_post("/api/messages", self._handle_webhook)

        self._runner = web.AppRunner(self._web_app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._webhook_port)
        await site.start()

        logger.info(
            f"[Teams] Webhook server started on port {self._webhook_port}. "
            f"Set messaging endpoint to <your_url>/api/messages"
        )

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            logger.info("[Teams] Webhook server stopped")

    @asynccontextmanager
    async def typing(self):
        """Show typing indicator in Teams while agent processes."""
        if self._active_turn_context:
            try:
                typing_activity = Activity(type=ActivityTypes.typing)
                await self._active_turn_context.send_activity(typing_activity)
            except Exception:
                pass
        yield

    # --- Internal handlers ---

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook from Azure Bot Service."""
        if "application/json" not in request.content_type:
            return web.Response(status=415)

        body = await request.json()
        activity = Activity().deserialize(body)

        auth_header = request.headers.get("Authorization", "")

        async def turn_callback(turn_context: TurnContext):
            await self._process_activity(turn_context)

        try:
            await self._adapter.process_activity(activity, auth_header, turn_callback)
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"[Teams] Error processing activity: {e}")
            return web.Response(status=500)

    async def _process_activity(self, turn_context: TurnContext) -> None:
        """Route incoming activities to the right handler."""
        activity = turn_context.activity

        if activity.type == ActivityTypes.message:
            # Save conversation reference for proactive messaging
            self._conversation_reference = TurnContext.get_conversation_reference(activity)
            self._active_turn_context = turn_context

            sender_id = activity.from_property.id if activity.from_property else ""
            sender_name = activity.from_property.name if activity.from_property else "Unknown"

            # Check authorization
            if self._allowed_users and sender_id not in self._allowed_users:
                logger.warning(f"[Teams] Ignoring unauthorized user: {sender_id}")
                return

            text = (activity.text or "").strip()

            # Check if this is a button callback (message_back)
            if activity.value and isinstance(activity.value, dict):
                action = activity.value.get("action", "")
                value = activity.value.get("value", "")
                future = self._pending_buttons.get(action)
                if future and not future.done():
                    future.set_result(value)
                    await turn_context.send_activity(f"Selected: {value}")
                    return

            # Also check text-based button callbacks (prefix:value format)
            if ":" in text and text.startswith("btn_"):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    prefix, value = parts
                    future = self._pending_buttons.get(prefix)
                    if future and not future.done():
                        future.set_result(value)
                        await turn_context.send_activity(f"Selected: {value}")
                        return

            # Regular message — forward to agent
            if text and self._on_message:
                incoming = IncomingMessage(
                    text=text,
                    sender_name=sender_name,
                    sender_id=sender_id,
                    raw=activity.as_dict() if hasattr(activity, 'as_dict') else {},
                )
                await self._on_message(incoming)

            # Clear active context after processing
            self._active_turn_context = None

        elif activity.type == ActivityTypes.conversation_update:
            # Bot was added to conversation — save reference
            if activity.members_added:
                for member in activity.members_added:
                    if member.id != activity.recipient.id:
                        self._conversation_reference = (
                            TurnContext.get_conversation_reference(activity)
                        )
                        logger.info(f"[Teams] User joined: {member.name}")


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
