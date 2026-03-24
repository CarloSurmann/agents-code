"""Gmail Provider — EmailProvider implementation using Google Gmail API.

Setup:
    Requires OAuth2 credentials.json + token.json.
    Set paths via env vars: GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH
    See: https://developers.google.com/gmail/api/quickstart/python

Usage:
    from agency.tools.email.gmail import GmailProvider

    gmail = GmailProvider()
    agent = Agent(tools=[*gmail.as_tools(), ...])
"""

from __future__ import annotations

import base64
import os
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from agency.tools.email.interface import EmailProvider, SentEmail, EmailMessage

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class GmailProvider(EmailProvider):
    """Gmail API implementation of EmailProvider."""

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
    ):
        self._creds_path = credentials_path or os.environ.get(
            "GMAIL_CREDENTIALS_PATH", "credentials.json"
        )
        self._token_path = token_path or os.environ.get(
            "GMAIL_TOKEN_PATH", "token.json"
        )
        self._service = None

    def _get_service(self):
        """Lazy-init the Gmail API service."""
        if self._service is not None:
            return self._service

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if os.path.exists(self._token_path):
            creds = Credentials.from_authorized_user_file(self._token_path, _SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self._creds_path, _SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self._token_path, "w") as f:
                f.write(creds.to_json())

        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _parse_headers(self, msg: dict) -> dict:
        """Extract headers from a Gmail message."""
        return {h["name"]: h["value"] for h in msg["payload"]["headers"]}

    def _extract_body(self, msg: dict) -> str:
        """Extract plain text body from a Gmail message."""
        payload = msg["payload"]
        if "parts" in payload:
            for part in payload["parts"]:
                if part["mimeType"] == "text/plain" and "data" in part.get("body", {}):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode()
        elif "body" in payload and "data" in payload["body"]:
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode()
        return ""

    def _parse_date(self, date_str: str) -> datetime:
        """Parse email date header to datetime."""
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return datetime.now()

    # --- EmailProvider interface ---

    def send_email(self, to: str, subject: str, body: str, cc: str = "", thread_id: str = "") -> str:
        service = self._get_service()

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body_payload = {"raw": raw}

        if thread_id:
            body_payload["threadId"] = thread_id

        sent = service.users().messages().send(userId="me", body=body_payload).execute()
        logger.info(f"[Gmail] Sent email to {to}: {subject}")
        return sent.get("id", "")

    def search_inbox(self, query: str, max_results: int = 10) -> list[EmailMessage]:
        service = self._get_service()

        response = service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        results = []
        for msg_ref in response.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            headers = self._parse_headers(msg)
            body_text = self._extract_body(msg)

            from_header = headers.get("From", "")
            from_name = from_header.split("<")[0].strip().strip('"') if "<" in from_header else ""
            from_email = from_header.split("<")[-1].rstrip(">") if "<" in from_header else from_header

            to_header = headers.get("To", "")
            to_name = to_header.split("<")[0].strip().strip('"') if "<" in to_header else ""
            to_email = to_header.split("<")[-1].rstrip(">") if "<" in to_header else to_header

            results.append(EmailMessage(
                message_id=msg["id"],
                thread_id=msg["threadId"],
                from_email=from_email,
                from_name=from_name,
                to_email=to_email,
                to_name=to_name,
                subject=headers.get("Subject", ""),
                body=body_text,
                date=self._parse_date(headers.get("Date", "")),
                is_reply=headers.get("Subject", "").lower().startswith("re:"),
            ))

        return results

    def read_message(self, message_id: str) -> EmailMessage:
        service = self._get_service()

        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = self._parse_headers(msg)
        body_text = self._extract_body(msg)

        from_header = headers.get("From", "")
        from_name = from_header.split("<")[0].strip().strip('"') if "<" in from_header else ""
        from_email = from_header.split("<")[-1].rstrip(">") if "<" in from_header else from_header

        to_header = headers.get("To", "")
        to_name = to_header.split("<")[0].strip().strip('"') if "<" in to_header else ""
        to_email = to_header.split("<")[-1].rstrip(">") if "<" in to_header else to_header

        return EmailMessage(
            message_id=msg["id"],
            thread_id=msg["threadId"],
            from_email=from_email,
            from_name=from_name,
            to_email=to_email,
            to_name=to_name,
            subject=headers.get("Subject", ""),
            body=body_text,
            date=self._parse_date(headers.get("Date", "")),
            is_reply=headers.get("Subject", "").lower().startswith("re:"),
        )

    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        service = self._get_service()

        query = f"in:sent after:{int(since.timestamp())}"
        response = service.users().messages().list(
            userId="me", q=query, maxResults=50
        ).execute()

        results = []
        for msg_ref in response.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            headers = self._parse_headers(msg)

            to_header = headers.get("To", "")
            to_name = to_header.split("<")[0].strip().strip('"') if "<" in to_header else ""
            to_email = to_header.split("<")[-1].rstrip(">") if "<" in to_header else to_header

            results.append(SentEmail(
                message_id=msg["id"],
                thread_id=msg["threadId"],
                to_email=to_email,
                to_name=to_name,
                subject=headers.get("Subject", ""),
                body=self._extract_body(msg),
                sent_date=self._parse_date(headers.get("Date", "")),
                has_attachments="parts" in msg["payload"] and len(msg["payload"]["parts"]) > 1,
            ))

        return sorted(results, key=lambda e: e.sent_date, reverse=True)

    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        service = self._get_service()

        thread = service.users().threads().get(userId="me", id=thread_id).execute()
        messages = thread.get("messages", [])

        if len(messages) <= 1:
            return False

        # Check if any message after the first one was received (not sent by us)
        profile = service.users().getProfile(userId="me").execute()
        my_email = profile.get("emailAddress", "")

        for msg in messages[1:]:
            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            from_email = headers.get("From", "")
            msg_date = self._parse_date(headers.get("Date", ""))

            if my_email not in from_email and msg_date > after:
                return True

        return False
