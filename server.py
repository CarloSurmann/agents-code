"""Server — receives messages from any channel and routes to the agent.

This is the entry point for running an agent in conversational mode.

Usage:
    # Telegram + Ollama (dev)
    python server.py

    # Telegram + Claude API (prod)
    AGENT_MODEL=claude-sonnet-4-6 python server.py

    # Console mode (no Telegram needed)
    CHANNEL=console python server.py

    # Slack
    CHANNEL=slack python server.py

Environment variables:
    CHANNEL              — "telegram" (default), "console", "slack", "whatsapp", "teams"
    AGENT_MODEL          — "ollama/qwen3.5:9b" (default) or "claude-sonnet-4-6"
    TELEGRAM_BOT_TOKEN   — from @BotFather
    TELEGRAM_CHAT_ID     — chat ID for the agent
    SLACK_BOT_TOKEN      — Slack bot token
    SLACK_APP_TOKEN      — Slack app token (Socket Mode)
    WHATSAPP_PHONE_ID    — from Meta Developer dashboard
    WHATSAPP_ACCESS_TOKEN — permanent token (System User for prod)
    WHATSAPP_VERIFY_TOKEN — any string (for webhook verification)
    WHATSAPP_APP_SECRET  — app secret (validates webhook signatures)
    TEAMS_APP_ID         — Azure Bot registration App ID
    TEAMS_APP_PASSWORD   — Azure Bot registration password/secret
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from agency.agent import Agent
from agency.channels.base import IncomingMessage
from agency.hooks.hitl import ChannelHITL
from agency.tracing import JSONTracer
from agency.tools.fattureincloud import get_overdue_invoices, check_payment_status, get_company_info

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an Accounts Receivable assistant. You help the business owner
manage overdue invoices by having a conversation with them.

Your workflow:
1. When asked, fetch overdue invoices using the get_overdue_invoices tool
2. Present a clear briefing: which invoices are overdue, how long, any context
3. Ask the owner what they'd like to do about each one
4. Draft chase emails based on their instructions
5. Show the draft to the owner for approval before sending
6. Send each email only after explicit approval

Finding the right email address:
- Each invoice includes a customer_email field from the accounting system
- If customer_email is empty, search the inbox for previous correspondence
  with that company name using search_inbox
- If you find past emails, suggest the contact to the owner for confirmation
- ALWAYS confirm the email address with the owner before sending
- If nothing found, ask: "I don't have a contact for [company] — who should I reach out to?"

Communication style:
- Be concise and clear — this is a chat, not a report
- Use bullet points and emojis for readability
- ABSOLUTELY NO TABLES — no pipes (|), no dashes (---), no grid formatting whatsoever. Tables are completely broken on WhatsApp/Telegram. Instead, list each invoice as a short bullet point like:
  🔴 Fattura 4/2026 — Verde Distribuzione SRL — €7.442 — scaduta da 33gg
- NEVER use markdown headers (#) — use bold (*text*) or emojis instead
- Suggest actions but always defer to the owner's judgment
- When presenting invoices, group by urgency, one per line as bullet points

Chase email guidelines:
- 1-6 days overdue: Friendly reminder. Casual tone.
- 7-13 days: Firm but warm. Ask for a payment date.
- 14-29 days: Formal. Reference previous reminders.
- 30+ days: Final notice. Serious but professional.

Rules:
- Never threaten legal action (that's a human decision)
- Always include invoice number, amount, and due date
- Respect the customer relationship
- If the owner says skip, skip without question
- Write chase emails in Italian unless told otherwise
"""


# ---------------------------------------------------------------------------
# Channel factory
# ---------------------------------------------------------------------------

def create_channel(channel_type: str):
    """Create the messaging channel based on config."""
    if channel_type == "telegram":
        from agency.channels.telegram import TelegramChannel
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        return TelegramChannel(allowed_chat_ids=[chat_id] if chat_id else None)
    elif channel_type == "slack":
        from agency.channels.slack import SlackChannel
        return SlackChannel()
    elif channel_type == "whatsapp":
        from agency.channels.whatsapp import WhatsAppChannel
        return WhatsAppChannel()
    elif channel_type == "teams":
        from agency.channels.teams import TeamsChannel
        return TeamsChannel()
    elif channel_type == "console":
        from agency.channels.console import ConsoleChannel
        return ConsoleChannel()
    else:
        raise ValueError(f"Unknown channel: {channel_type}. Use: telegram, slack, whatsapp, teams, console")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    model = os.environ.get("AGENT_MODEL", "ollama/qwen3.5:9b")
    channel_type = os.environ.get("CHANNEL", "telegram")

    # Set up channel
    channel = create_channel(channel_type)

    # Set up HITL
    hitl = ChannelHITL(
        channel=channel,
        gated_tools=["send_email"],
    )

    # Set up tracer
    tracer = JSONTracer()

    # Build agent
    agent = Agent(
        name="ar-follow-up",
        system_prompt=SYSTEM_PROMPT,
        model=model,
        tools=[
            get_overdue_invoices,
            check_payment_status,
            get_company_info,
        ],
        hooks=[hitl],
        tracer=tracer,
    )

    # Handle incoming messages
    async def handle_message(msg: IncomingMessage):
        logger.info(f"Received from {msg.sender_name}: {msg.text}")

        async with channel.typing():
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, agent.run, msg.text)

        if result.output:
            await channel.send_message(result.output)

        if result.trace_file:
            logger.info(f"Trace saved: {result.trace_file}")

    # Start
    logger.info(f"Starting AR Follow-Up agent (model: {model}, channel: {channel_type})")
    await channel.start(handle_message)

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        await channel.stop()


if __name__ == "__main__":
    asyncio.run(main())
