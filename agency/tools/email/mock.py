"""Mock Email Provider — For testing without real email accounts.

Stores everything in memory. Perfect for evals and development.
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field

from agency.tools.email.interface import EmailProvider, SentEmail, EmailMessage

logger = logging.getLogger(__name__)


class MockProvider(EmailProvider):
    """In-memory email provider for testing."""

    def __init__(self):
        self._sent: list[dict] = []
        self._inbox: list[EmailMessage] = []
        self._sent_folder: list[SentEmail] = []

    def seed_inbox(self, messages: list[EmailMessage]):
        """Pre-populate inbox with test messages."""
        self._inbox.extend(messages)

    def seed_sent_folder(self, emails: list[SentEmail]):
        """Pre-populate sent folder with test emails."""
        self._sent_folder.extend(emails)

    def send_email(self, to: str, subject: str, body: str, cc: str = "", thread_id: str = "") -> str:
        msg_id = f"mock_{len(self._sent)}"
        self._sent.append({
            "message_id": msg_id,
            "to": to,
            "subject": subject,
            "body": body,
            "cc": cc,
            "thread_id": thread_id,
            "sent_at": datetime.now().isoformat(),
        })
        logger.info(f"[MockEmail] Sent to {to}: {subject}")
        return msg_id

    def search_inbox(self, query: str, max_results: int = 10) -> list[EmailMessage]:
        query_lower = query.lower()
        results = [
            m for m in self._inbox
            if query_lower in m.subject.lower()
            or query_lower in m.body.lower()
            or query_lower in m.from_email.lower()
            or query_lower in m.from_name.lower()
        ]
        return results[:max_results]

    def read_message(self, message_id: str) -> EmailMessage:
        for m in self._inbox:
            if m.message_id == message_id:
                return m
        raise ValueError(f"Message {message_id} not found")

    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        return [e for e in self._sent_folder if e.sent_date >= since]

    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        return any(
            m.thread_id == thread_id and m.date > after and m.is_reply
            for m in self._inbox
        )

    def get_sent_emails(self) -> list[dict]:
        """Test helper: inspect what the agent actually sent."""
        return list(self._sent)
