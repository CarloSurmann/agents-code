"""Messaging channels — how the agent talks to humans.

Each channel implements the same interface (Channel base class).
The agent doesn't know or care which channel it's using.

Available channels:
- TelegramChannel — via python-telegram-bot
- SlackChannel — via slack_bolt (Socket Mode)
- ConsoleChannel — terminal stdin/stdout (dev/testing)
- WhatsApp — coming (Meta Cloud API via pywa)
- Teams — coming (Microsoft Graph API)
"""

from agency.channels.base import Channel, IncomingMessage
