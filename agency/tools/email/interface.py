"""Email Provider Interface — Abstract base class for all email backends.

Every email provider (Gmail, Microsoft Graph, IMAP) implements this interface.
The agent code calls these methods without knowing which backend is running.

This is the "universal adapter" pattern — designed by Carlo, adopted across
both AR Follow-Up and Email Follow-Up agents.

To add a new provider:
1. Create a new file (e.g., imap.py)
2. Subclass EmailProvider
3. Implement all abstract methods
4. Done — plug it into any agent via config
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SentEmail:
    """A sent email detected by the watcher."""
    message_id: str
    thread_id: str
    to_email: str
    to_name: str
    subject: str
    body: str
    sent_date: datetime
    has_attachments: bool = False
    attachment_names: list[str] = field(default_factory=list)


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
    """Abstract base class for email providers.

    Implementations: GmailProvider, OutlookProvider, MockProvider.
    """

    @abstractmethod
    def send_email(self, to: str, subject: str, body: str, cc: str = "", thread_id: str = "") -> str:
        """Send an email (new or reply in existing thread).

        Args:
            to: Recipient email address
            subject: Email subject line
            body: Email body (plain text)
            cc: CC recipients (comma-separated, optional)
            thread_id: If provided, sends as reply in existing thread

        Returns:
            The message ID of the sent email
        """
        ...

    @abstractmethod
    def search_inbox(self, query: str, max_results: int = 10) -> list[EmailMessage]:
        """Search the inbox for messages matching a query.

        Args:
            query: Search query (provider-specific syntax)
            max_results: Maximum number of results to return

        Returns:
            List of matching email messages
        """
        ...

    @abstractmethod
    def read_message(self, message_id: str) -> EmailMessage:
        """Read the full content of a specific email.

        Args:
            message_id: Provider-specific message ID

        Returns:
            Full email message content
        """
        ...

    @abstractmethod
    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        """Get emails sent since the given timestamp.

        Used by Email Follow-Up agent to detect new outbound emails
        that need follow-up tracking.

        Args:
            since: Only return emails sent after this time

        Returns:
            List of sent emails, newest first
        """
        ...

    @abstractmethod
    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        """Check if anyone has replied in a thread since the given date.

        Args:
            thread_id: The thread/conversation to check
            after: Only look for replies after this time

        Returns:
            True if a reply was found, False otherwise
        """
        ...

    # ----- Convenience: wrap provider methods as agent tools -----

    def as_tools(self) -> list:
        """Return bound methods as a list of tool functions for the agent.

        Usage:
            email = GmailProvider(...)
            agent = Agent(tools=[*email.as_tools(), other_tool, ...])
        """
        # Create standalone functions with proper names and docstrings
        # so the agent's tool schema generator can inspect them

        def send_email(to: str, subject: str, body: str, cc: str = "", thread_id: str = "") -> str:
            """Send an email. If thread_id is provided, sends as a reply in that thread."""
            return self.send_email(to, subject, body, cc, thread_id)

        def search_inbox(query: str, max_results: int = 10) -> list:
            """Search inbox for emails matching the query. Returns list of messages."""
            results = self.search_inbox(query, max_results)
            return [
                {
                    "message_id": m.message_id,
                    "thread_id": m.thread_id,
                    "from": f"{m.from_name} <{m.from_email}>",
                    "to": f"{m.to_name} <{m.to_email}>",
                    "subject": m.subject,
                    "body": m.body[:500],
                    "date": m.date.isoformat(),
                    "is_reply": m.is_reply,
                }
                for m in results
            ]

        def read_message(message_id: str) -> dict:
            """Read the full content of a specific email by its message ID."""
            m = self.read_message(message_id)
            return {
                "message_id": m.message_id,
                "thread_id": m.thread_id,
                "from": f"{m.from_name} <{m.from_email}>",
                "to": f"{m.to_name} <{m.to_email}>",
                "subject": m.subject,
                "body": m.body,
                "date": m.date.isoformat(),
                "is_reply": m.is_reply,
            }

        return [send_email, search_inbox, read_message]
