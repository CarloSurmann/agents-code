"""
Email Provider Interface — Abstract base class for all email backends.

Every email provider (Gmail, Microsoft Graph, IMAP) implements this interface.
The agent code calls these methods without knowing which backend is running.

This is the "universal adapter" pattern.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SentEmail:
    """A sent email detected by the watcher."""
    message_id: str       # Provider-specific message ID
    thread_id: str        # Provider-specific thread/conversation ID
    to_email: str         # Recipient email address
    to_name: str          # Recipient display name (if available)
    subject: str          # Email subject line
    body: str             # Email body (plain text)
    sent_date: datetime   # When the email was sent
    has_attachments: bool = False
    attachment_names: list[str] | None = None


@dataclass
class EmailMessage:
    """Full email message content."""
    message_id: str
    thread_id: str
    from_email: str
    from_name: str
    to_email: str
    to_name: str
    subject: str
    body: str
    date: datetime
    is_reply: bool = False


class EmailProvider(ABC):
    """
    Abstract base class for email providers.

    Implementations: GmailProvider, MSGraphProvider, IMAPProvider.
    """

    @abstractmethod
    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        """
        Get emails sent since the given timestamp.

        Args:
            since: Only return emails sent after this time

        Returns:
            List of sent emails, newest first
        """
        ...

    @abstractmethod
    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        """
        Check if anyone has replied in a thread since the given date.

        Args:
            thread_id: The thread/conversation to check
            after: Only look for replies after this time

        Returns:
            True if a reply was found, False otherwise
        """
        ...

    @abstractmethod
    def send_reply(
        self,
        thread_id: str,
        message_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """
        Send a reply within an existing email thread.

        CRITICAL: Must use In-Reply-To and References headers (Gmail)
        or createReply endpoint (Graph) to keep the reply in-thread.

        Args:
            thread_id: Thread to reply in
            message_id: Message to reply to (for In-Reply-To header)
            to: Recipient email
            subject: Subject line (should start with "Re: ")
            body: Email body text

        Returns:
            The new message ID of the sent reply
        """
        ...

    @abstractmethod
    def read_message(self, message_id: str) -> EmailMessage:
        """
        Read the full content of a specific email.

        Args:
            message_id: Provider-specific message ID

        Returns:
            Full email message content
        """
        ...
