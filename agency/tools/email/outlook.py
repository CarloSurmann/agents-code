"""Outlook Provider — EmailProvider implementation using Microsoft Graph API.

Setup:
    1. Register an app in Azure AD (portal.azure.com → App registrations)
    2. Add permissions: Mail.Read, Mail.Send, Mail.ReadWrite
    3. Set MS_CLIENT_ID, MS_TENANT_ID in .env
    4. For dev: set MS_USE_DEVICE_CODE=true (one-time browser login)
    5. For prod: set MS_CLIENT_SECRET (client credentials, no user interaction)

Usage:
    from agency.tools.email.outlook import OutlookProvider

    outlook = OutlookProvider()
    agent = Agent(tools=[*outlook.as_tools(), ...])
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import msal
import requests

from agency.tools.email.interface import EmailProvider, SentEmail, EmailMessage

logger = logging.getLogger(__name__)

_BASE_URL = "https://graph.microsoft.com/v1.0"


class OutlookProvider(EmailProvider):
    """Microsoft Graph API implementation of EmailProvider."""

    def __init__(
        self,
        client_id: str | None = None,
        tenant_id: str | None = None,
        client_secret: str | None = None,
        user_email: str | None = None,
    ):
        self._client_id = client_id or os.environ.get("MS_CLIENT_ID", "")
        self._tenant_id = tenant_id or os.environ.get("MS_TENANT_ID", "")
        self._client_secret = client_secret or os.environ.get("MS_CLIENT_SECRET", "")
        self._user_email = user_email or os.environ.get("MS_USER_EMAIL", "me")
        self._token: str | None = os.environ.get("MS_ACCESS_TOKEN") or None

    def _get_token(self) -> str:
        if self._token:
            return self._token

        if not self._client_id or not self._tenant_id:
            raise RuntimeError("MS_CLIENT_ID and MS_TENANT_ID required")

        authority = f"https://login.microsoftonline.com/{self._tenant_id}"

        if self._client_secret:
            app = msal.ConfidentialClientApplication(
                self._client_id, authority=authority, client_credential=self._client_secret,
            )
            result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        else:
            app = msal.PublicClientApplication(self._client_id, authority=authority)
            accounts = app.get_accounts()
            if accounts:
                result = app.acquire_token_silent(
                    scopes=["Mail.Read", "Mail.Send", "Mail.ReadWrite"],
                    account=accounts[0],
                )
                if result and "access_token" in result:
                    self._token = result["access_token"]
                    return self._token

            flow = app.initiate_device_flow(scopes=["Mail.Read", "Mail.Send", "Mail.ReadWrite"])
            logger.info(f"Auth required: {flow['message']}")
            print(f"\n{flow['message']}\n")
            result = app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(f"Microsoft auth failed: {result.get('error_description', 'Unknown')}")

        self._token = result["access_token"]
        return self._token

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}
        user = self._user_email
        base = f"{_BASE_URL}/me" if user == "me" else f"{_BASE_URL}/users/{user}"
        response = requests.request(method, f"{base}{endpoint}", headers=headers, **kwargs)

        if response.status_code >= 400:
            logger.error(f"Graph API {response.status_code}: {response.text[:300]}")
            return {"error": response.text[:300]}
        if response.status_code == 204:
            return {"success": True}
        return response.json()

    def _parse_datetime(self, dt_str: str) -> datetime:
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return datetime.now()

    # --- EmailProvider interface ---

    def send_email(self, to: str, subject: str, body: str, cc: str = "", thread_id: str = "") -> str:
        message: dict = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
        if cc:
            message["ccRecipients"] = [{"emailAddress": {"address": cc}}]

        if thread_id:
            # Reply in existing conversation
            result = self._request("POST", f"/messages/{thread_id}/reply", json={"comment": body})
        else:
            result = self._request("POST", "/sendMail", json={"message": message, "saveToSentItems": True})

        logger.info(f"[Outlook] Sent email to {to}: {subject}")
        return thread_id or "sent"

    def search_inbox(self, query: str, max_results: int = 10) -> list[EmailMessage]:
        result = self._request("GET", "/messages", params={
            "$search": f'"{query}"',
            "$top": max_results,
            "$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,body",
            "$orderby": "receivedDateTime desc",
        })

        messages = []
        for msg in result.get("value", []):
            from_info = msg.get("from", {}).get("emailAddress", {})
            to_list = msg.get("toRecipients", [])
            to_info = to_list[0].get("emailAddress", {}) if to_list else {}

            messages.append(EmailMessage(
                message_id=msg.get("id", ""),
                thread_id=msg.get("conversationId", ""),
                from_email=from_info.get("address", ""),
                from_name=from_info.get("name", ""),
                to_email=to_info.get("address", ""),
                to_name=to_info.get("name", ""),
                subject=msg.get("subject", ""),
                body=msg.get("bodyPreview", ""),
                date=self._parse_datetime(msg.get("receivedDateTime", "")),
                is_reply=msg.get("subject", "").lower().startswith("re:"),
            ))

        return messages

    def read_message(self, message_id: str) -> EmailMessage:
        msg = self._request("GET", f"/messages/{message_id}", params={
            "$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,body",
        })

        from_info = msg.get("from", {}).get("emailAddress", {})
        to_list = msg.get("toRecipients", [])
        to_info = to_list[0].get("emailAddress", {}) if to_list else {}

        return EmailMessage(
            message_id=msg.get("id", ""),
            thread_id=msg.get("conversationId", ""),
            from_email=from_info.get("address", ""),
            from_name=from_info.get("name", ""),
            to_email=to_info.get("address", ""),
            to_name=to_info.get("name", ""),
            subject=msg.get("subject", ""),
            body=msg.get("body", {}).get("content", ""),
            date=self._parse_datetime(msg.get("receivedDateTime", "")),
            is_reply=msg.get("subject", "").lower().startswith("re:"),
        )

    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self._request("GET", "/mailFolders/sentItems/messages", params={
            "$filter": f"sentDateTime ge {since_str}",
            "$top": 50,
            "$select": "id,conversationId,subject,toRecipients,sentDateTime,bodyPreview,hasAttachments",
            "$orderby": "sentDateTime desc",
        })

        emails = []
        for msg in result.get("value", []):
            to_list = msg.get("toRecipients", [])
            to_info = to_list[0].get("emailAddress", {}) if to_list else {}

            emails.append(SentEmail(
                message_id=msg.get("id", ""),
                thread_id=msg.get("conversationId", ""),
                to_email=to_info.get("address", ""),
                to_name=to_info.get("name", ""),
                subject=msg.get("subject", ""),
                body=msg.get("bodyPreview", ""),
                sent_date=self._parse_datetime(msg.get("sentDateTime", "")),
                has_attachments=msg.get("hasAttachments", False),
            ))

        return emails

    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        result = self._request("GET", "/messages", params={
            "$filter": f"conversationId eq '{thread_id}'",
            "$select": "id,from,receivedDateTime",
            "$orderby": "receivedDateTime desc",
            "$top": 10,
        })

        for msg in result.get("value", []):
            msg_date = self._parse_datetime(msg.get("receivedDateTime", ""))
            from_addr = msg.get("from", {}).get("emailAddress", {}).get("address", "")

            if msg_date > after and self._user_email not in from_addr:
                return True

        return False
