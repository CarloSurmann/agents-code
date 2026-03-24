"""
Gmail Email Provider — Gmail API implementation of EmailProvider.

Uses the Gmail API via google-api-python-client.
Handles OAuth2 authentication, sent folder watching, thread-aware replies.

CRITICAL DETAIL: Follow-up emails are sent as replies in the original thread
using In-Reply-To and References headers. This makes them appear as natural
conversation continuations, not separate automated emails.
"""

import base64
import logging
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from agency.tools.email.interface import EmailProvider, SentEmail, EmailMessage

logger = logging.getLogger(__name__)

# Gmail API scopes needed
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailProvider(EmailProvider):
    """
    Gmail API implementation.

    Setup:
    1. Create OAuth2 credentials in Google Cloud Console
    2. Download credentials.json to secrets/
    3. Run once interactively to complete OAuth flow (creates token.json)
    4. After that, runs headlessly using the refresh token
    """

    def __init__(self, credentials_path: str, token_path: str, user_email: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.user_email = user_email
        self._service = None

    def _get_service(self):
        """Get authenticated Gmail API service (lazy init + token refresh)."""
        if self._service:
            return self._service

        creds = None
        token_path = Path(self.token_path)

        # Load existing token
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        # Refresh or create new token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing Gmail OAuth token...")
                creds.refresh(Request())
            else:
                logger.info("Starting Gmail OAuth flow (interactive)...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for next time
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json())
            logger.info(f"Token saved to {token_path}")

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _parse_message(self, msg: dict) -> dict:
        """Extract headers and body from a Gmail API message resource."""
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

        # Extract body — handle both simple and multipart messages
        body = ""
        payload = msg.get("payload", {})

        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                    break
            # Fallback to text/html if no plain text
            if not body:
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break

        # Parse sender/recipient
        from_raw = headers.get("from", "")
        to_raw = headers.get("to", "")

        def parse_email_field(raw: str) -> tuple[str, str]:
            """Parse 'Name <email>' into (name, email)."""
            if "<" in raw and ">" in raw:
                name = raw.split("<")[0].strip().strip('"')
                email = raw.split("<")[1].split(">")[0].strip()
                return name, email
            return "", raw.strip()

        from_name, from_email = parse_email_field(from_raw)
        to_name, to_email = parse_email_field(to_raw)

        # Parse date
        internal_date_ms = int(msg.get("internalDate", "0"))
        date = datetime.fromtimestamp(internal_date_ms / 1000)

        # Check attachments
        has_attachments = False
        attachment_names = []
        for part in payload.get("parts", []):
            if part.get("filename"):
                has_attachments = True
                attachment_names.append(part["filename"])

        return {
            "message_id": msg["id"],
            "thread_id": msg["threadId"],
            "from_name": from_name,
            "from_email": from_email,
            "to_name": to_name,
            "to_email": to_email,
            "subject": headers.get("subject", "(no subject)"),
            "body": body,
            "date": date,
            "has_attachments": has_attachments,
            "attachment_names": attachment_names,
            "rfc_message_id": headers.get("message-id", ""),
        }

    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        """Get emails sent since the given timestamp from the SENT folder."""
        service = self._get_service()

        # Gmail uses epoch seconds for after: query
        after_epoch = int(since.timestamp())
        query = f"in:sent after:{after_epoch}"

        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=50
            ).execute()
        except Exception as e:
            logger.error(f"Gmail API error listing sent: {e}")
            return []

        messages = results.get("messages", [])
        if not messages:
            return []

        sent_emails = []
        for msg_ref in messages:
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()

                parsed = self._parse_message(msg)

                sent_emails.append(SentEmail(
                    message_id=parsed["message_id"],
                    thread_id=parsed["thread_id"],
                    to_email=parsed["to_email"],
                    to_name=parsed["to_name"],
                    subject=parsed["subject"],
                    body=parsed["body"],
                    sent_date=parsed["date"],
                    has_attachments=parsed["has_attachments"],
                    attachment_names=parsed["attachment_names"],
                ))
            except Exception as e:
                logger.error(f"Error parsing sent message {msg_ref['id']}: {e}")

        logger.info(f"Found {len(sent_emails)} sent emails since {since.isoformat()}")
        return sent_emails

    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        """Check if anyone (other than us) has replied in a thread since the given date."""
        service = self._get_service()

        try:
            thread = service.users().threads().get(
                userId="me", id=thread_id, format="metadata",
                metadataHeaders=["From", "Date"]
            ).execute()
        except Exception as e:
            logger.error(f"Error fetching thread {thread_id}: {e}")
            return False

        messages = thread.get("messages", [])

        for msg in messages:
            internal_date_ms = int(msg.get("internalDate", "0"))
            msg_date = datetime.fromtimestamp(internal_date_ms / 1000)

            if msg_date <= after:
                continue

            # Check if the message is from someone else (not us)
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            from_email = headers.get("from", "")

            if self.user_email.lower() not in from_email.lower():
                logger.info(f"Reply found in thread {thread_id} from {from_email}")
                return True

        return False

    def send_reply(
        self,
        thread_id: str,
        message_id: str,
        to: str,
        subject: str,
        body: str,
    ) -> str:
        """
        Send a reply in an existing thread.

        CRITICAL: Uses In-Reply-To and References headers to keep it in-thread.
        Without these headers, the reply appears as a separate email.
        """
        service = self._get_service()

        # First, get the original message's RFC Message-ID header
        try:
            original = service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["Message-ID", "References"]
            ).execute()
            headers = {h["name"]: h["value"] for h in original.get("payload", {}).get("headers", [])}
            rfc_msg_id = headers.get("Message-ID", "")
            references = headers.get("References", "")
        except Exception:
            rfc_msg_id = ""
            references = ""

        # Build the MIME message with thread headers
        mime_msg = MIMEText(body)
        mime_msg["to"] = to
        mime_msg["from"] = self.user_email
        mime_msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"

        # These headers are what make Gmail/Outlook thread the reply correctly
        if rfc_msg_id:
            mime_msg["In-Reply-To"] = rfc_msg_id
            mime_msg["References"] = f"{references} {rfc_msg_id}".strip()

        # Encode and send
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")

        try:
            sent = service.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()

            new_msg_id = sent["id"]
            logger.info(f"Sent reply in thread {thread_id}: {new_msg_id}")
            return new_msg_id

        except Exception as e:
            logger.error(f"Error sending reply in thread {thread_id}: {e}")
            raise

    def read_message(self, message_id: str) -> EmailMessage:
        """Read the full content of a specific email."""
        service = self._get_service()

        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

        parsed = self._parse_message(msg)

        return EmailMessage(
            message_id=parsed["message_id"],
            thread_id=parsed["thread_id"],
            from_email=parsed["from_email"],
            from_name=parsed["from_name"],
            to_email=parsed["to_email"],
            to_name=parsed["to_name"],
            subject=parsed["subject"],
            body=parsed["body"],
            date=parsed["date"],
        )


# ─── Tool functions exposed to the Agent ─────────────────────────────
# These are standalone functions that the Agent auto-converts to tool schemas.
# They delegate to a GmailProvider instance stored in module-level state.

_provider: GmailProvider | None = None


def init_gmail_provider(credentials_path: str, token_path: str, user_email: str):
    """Initialize the Gmail provider. Call this before using tool functions."""
    global _provider
    _provider = GmailProvider(credentials_path, token_path, user_email)


def _get_provider() -> GmailProvider:
    if _provider is None:
        raise RuntimeError("Gmail provider not initialized. Call init_gmail_provider() first.")
    return _provider


def watch_sent_folder(since_iso: str) -> str:
    """Get a list of emails sent from your mailbox since the given ISO timestamp. Use this to discover new outbound emails that may need follow-up tracking."""
    provider = _get_provider()
    since = datetime.fromisoformat(since_iso)
    emails = provider.watch_sent_folder(since)
    if not emails:
        return "No new sent emails found."

    results = []
    for e in emails:
        results.append({
            "message_id": e.message_id,
            "thread_id": e.thread_id,
            "to": f"{e.to_name} <{e.to_email}>",
            "subject": e.subject,
            "sent_date": e.sent_date.isoformat(),
            "body_preview": e.body[:300],
            "has_attachments": e.has_attachments,
        })
    return results


def check_thread_for_reply(thread_id: str, after_iso: str) -> str:
    """Check if anyone has replied in the given email thread since the specified date. Returns true if a reply was found from someone other than the sender."""
    provider = _get_provider()
    after = datetime.fromisoformat(after_iso)
    has_reply = provider.check_thread_for_reply(thread_id, after)
    return {"thread_id": thread_id, "has_reply": has_reply}


def send_follow_up_reply(thread_id: str, message_id: str, to: str, subject: str, body: str) -> str:
    """Send a follow-up email as a reply in the original thread. IMPORTANT: This sends a real email. The reply will appear in the same conversation thread as the original email, making it look natural."""
    provider = _get_provider()
    new_id = provider.send_reply(thread_id, message_id, to, subject, body)
    return {"status": "sent", "new_message_id": new_id, "thread_id": thread_id}


def read_email_message(message_id: str) -> str:
    """Read the full content of a specific email by its message ID."""
    provider = _get_provider()
    msg = provider.read_message(message_id)
    return {
        "message_id": msg.message_id,
        "thread_id": msg.thread_id,
        "from": f"{msg.from_name} <{msg.from_email}>",
        "to": f"{msg.to_name} <{msg.to_email}>",
        "subject": msg.subject,
        "body": msg.body,
        "date": msg.date.isoformat(),
    }
