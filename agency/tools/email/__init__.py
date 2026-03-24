"""Email provider interface and implementations.

Usage:
    from agency.tools.email import GmailProvider, OutlookProvider, MockProvider

    # Pick a provider based on client config
    email = GmailProvider(credentials_path="credentials.json")
    # or
    email = OutlookProvider(tenant_id="...", client_id="...")
    # or (for testing)
    email = MockProvider()

    # Agent tools are the same regardless of provider:
    emails = email.watch_sent_folder(since=yesterday)
    has_reply = email.check_thread_for_reply(thread_id, after=sent_date)
    email.send_reply(thread_id, message_id, to, subject, body)
"""

from agency.tools.email.interface import (
    EmailProvider,
    SentEmail,
    EmailMessage,
)

__all__ = ["EmailProvider", "SentEmail", "EmailMessage"]
