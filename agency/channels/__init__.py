"""Messaging channels — how the agent talks to humans.

Each channel implements the same interface (Channel base class).
The agent doesn't know or care which channel it's using.

Available channels:
- TelegramChannel — via python-telegram-bot
- SlackChannel — via slack_bolt (Socket Mode)
- WhatsAppChannel — Meta Cloud API via pywa (webhook-based)
- TeamsChannel — Microsoft Bot Framework SDK v4 (webhook-based)
- ConsoleChannel — terminal stdin/stdout (dev/testing)
"""

from agency.channels.base import Channel, IncomingMessage
