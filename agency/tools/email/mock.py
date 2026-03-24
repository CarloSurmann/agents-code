"""
Mock Email Provider — Fake Gmail for testing without real API calls.

Generates realistic fake email data. Simulates:
- Sent folder with a mix of trackable and non-trackable emails
- Thread replies (some threads get responses, some don't)
- Follow-up sending (logs instead of actually sending)

Use this to test the full agent loop 100x without hitting Gmail or burning tokens.
"""

import json
import logging
import random
from datetime import datetime, timedelta

from agency.tools.email.interface import EmailProvider, SentEmail, EmailMessage

logger = logging.getLogger(__name__)


# ─── Realistic fake email data ─────────────────────────────────────

FAKE_CONTACTS = [
    ("Giovanni Rossi", "giovanni@logistics.nl"),
    ("Maria Schmidt", "maria@techfirm.de"),
    ("Sophie Laurent", "sophie@distribfr.com"),
    ("Jan de Vries", "jan@warehouse.nl"),
    ("Emma Wilson", "emma@consulting.co.uk"),
    ("Luca Bianchi", "luca@manufacturing.it"),
    ("Thomas Müller", "thomas@automotive.de"),
    ("Ana Garcia", "ana@retail.es"),
    ("Peter Bakker", "peter@staffing.nl"),
    ("Claire Dubois", "claire@pharma.fr"),
]

# Emails that SHOULD be tracked (proposals, questions, requests)
TRACKABLE_EMAILS = [
    {"subject": "Proposal for Q2 warehouse optimization",
     "body": "Hi {name},\n\nFollowing our conversation last week, I've prepared a detailed proposal:\n- Bottleneck analysis of current routing\n- AI-driven optimization approach\n- Expected 15-20% efficiency improvement\n- 6-week implementation timeline\n\nTotal investment: €{amount} + €1,200/month maintenance.\n\nCan we schedule a call this week to discuss?\n\nBest regards,\nCarlo",
     "type": "proposal", "has_attachments": True},
    {"subject": "Quote for ERP integration project",
     "body": "Hi {name},\n\nAs discussed, here's the quote for integrating your {system} with our automation platform:\n\nPhase 1: Data mapping & setup — €{amount}\nPhase 2: Integration & testing — €{amount2}\nPhase 3: Go-live & training — €3,500\n\nTotal: €{total}\nTimeline: 4 weeks\n\nLet me know if you'd like to proceed.\n\nBest,\nCarlo",
     "type": "quote", "has_attachments": True},
    {"subject": "Quick question about your current workflow",
     "body": "Hi {name},\n\nI was reviewing the process documentation you shared. One question:\n\nAre your incoming orders currently processed manually in {system}, or do you have any automation in place?\n\nThis will help me scope the next phase of work.\n\nThanks,\nCarlo",
     "type": "question", "has_attachments": False},
    {"subject": "Invoice #{invoice_num} — March consulting",
     "body": "Hi {name},\n\nPlease find attached the invoice for March consulting work.\n\nAmount: €{amount}\nPayment terms: Net 30 days\nBank: NL42 ABNA 0123 4567 89\n\nLet me know if you have any questions.\n\nBest,\nCarlo",
     "type": "invoice", "has_attachments": True},
    {"subject": "Follow-up: Meeting request about AI automation",
     "body": "Hi {name},\n\nI'd love to schedule a 30-minute call to discuss how AI automation could help streamline your {process}.\n\nWould any of these times work?\n- Tuesday 2pm CET\n- Wednesday 10am CET\n- Thursday 3pm CET\n\nLooking forward to connecting.\n\nBest,\nCarlo",
     "type": "request", "has_attachments": False},
]

# Emails that SHOULD NOT be tracked
NON_TRACKABLE_EMAILS = [
    {"subject": "Re: Meeting notes", "body": "Thanks!", "type": "ack", "has_attachments": False},
    {"subject": "Re: Project update", "body": "Got it, sounds good.", "type": "ack", "has_attachments": False},
    {"subject": "Re: Contract review", "body": "Perfect, will review and get back to you.", "type": "ack", "has_attachments": False},
    {"subject": "Updated meeting notes from today's sync", "body": "Hey team,\n\nAttached are the notes from today's standup.\n\nCheers,\nCarlo", "type": "internal_fyi", "has_attachments": True},
    {"subject": "FYI: Industry report on logistics automation", "body": "Hi {name},\n\nThought you might find this interesting — no action needed.\n\nBest,\nCarlo", "type": "fyi", "has_attachments": True},
    {"subject": "Happy holidays!", "body": "Hi {name},\n\nWishing you and the team a wonderful holiday season!\n\nBest,\nCarlo", "type": "social", "has_attachments": False},
    {"subject": "Accepted: Weekly sync — Tuesday 10am", "body": "", "type": "calendar", "has_attachments": False},
    {"subject": "Out of office: Back on March 28", "body": "Hi,\n\nI'm currently out of office until March 28. For urgent matters, please contact Giovanni.\n\nBest,\nCarlo", "type": "auto_reply", "has_attachments": False},
]

SYSTEMS = ["Odoo", "SAP Business One", "Exact Online", "AFAS", "Microsoft Dynamics"]
PROCESSES = ["order processing", "invoicing workflow", "inventory management", "HR onboarding", "customer support"]


class MockEmailProvider(EmailProvider):
    """
    Mock email provider for testing.

    Generates realistic fake data. Tracks what was "sent" for verification.
    """

    def __init__(self, user_email: str = "carlo@aiagency.com", reply_probability: float = 0.3, seed: int = 42):
        self.user_email = user_email
        self.reply_probability = reply_probability
        self._rng = random.Random(seed)
        self._sent_log: list[dict] = []  # Track emails "sent" by the agent
        self._generated_emails: list[SentEmail] = []
        self._thread_replies: dict[str, bool] = {}  # thread_id → has_reply

    def generate_emails(self, count: int = 10, days_back: int = 7) -> list[SentEmail]:
        """Pre-generate a batch of fake sent emails."""
        emails = []
        now = datetime.now()

        for i in range(count):
            # Mix: ~60% trackable, ~40% non-trackable
            if self._rng.random() < 0.6:
                template = self._rng.choice(TRACKABLE_EMAILS)
            else:
                template = self._rng.choice(NON_TRACKABLE_EMAILS)

            name, email_addr = self._rng.choice(FAKE_CONTACTS)
            first_name = name.split()[0]

            # Fill template
            body = template["body"].format(
                name=first_name,
                amount=self._rng.randint(5, 25) * 1000,
                amount2=self._rng.randint(3, 15) * 1000,
                total=self._rng.randint(10, 40) * 1000,
                system=self._rng.choice(SYSTEMS),
                process=self._rng.choice(PROCESSES),
                invoice_num=f"2024-{self._rng.randint(100, 999)}",
            )

            # Random send date within the window
            hours_ago = self._rng.randint(1, days_back * 24)
            sent_date = now - timedelta(hours=hours_ago)

            thread_id = f"mock-thread-{i:04d}"

            # Decide if this thread will have a reply
            self._thread_replies[thread_id] = self._rng.random() < self.reply_probability

            se = SentEmail(
                message_id=f"mock-msg-{i:04d}",
                thread_id=thread_id,
                to_email=email_addr,
                to_name=name,
                subject=template["subject"].format(
                    invoice_num=f"2024-{self._rng.randint(100, 999)}"
                ) if "{invoice_num}" in template["subject"] else template["subject"],
                body=body,
                sent_date=sent_date,
                has_attachments=template["has_attachments"],
                attachment_names=["proposal.pdf"] if template["has_attachments"] else None,
            )
            emails.append(se)

        self._generated_emails = emails
        return emails

    def watch_sent_folder(self, since: datetime) -> list[SentEmail]:
        """Return pre-generated emails that were 'sent' after the given time."""
        if not self._generated_emails:
            self.generate_emails()

        return [e for e in self._generated_emails if e.sent_date >= since]

    def check_thread_for_reply(self, thread_id: str, after: datetime) -> bool:
        """Check if the mock thread has a 'reply'."""
        return self._thread_replies.get(thread_id, False)

    def send_reply(self, thread_id: str, message_id: str, to: str, subject: str, body: str) -> str:
        """Log the 'sent' email instead of actually sending."""
        new_id = f"mock-sent-{len(self._sent_log):04d}"
        entry = {
            "new_message_id": new_id,
            "thread_id": thread_id,
            "in_reply_to": message_id,
            "to": to,
            "subject": subject,
            "body": body,
            "sent_at": datetime.now().isoformat(),
        }
        self._sent_log.append(entry)
        logger.info(f"[MOCK] Sent reply in {thread_id} to {to}: {subject}")
        return new_id

    def read_message(self, message_id: str) -> EmailMessage:
        """Read a pre-generated email."""
        for e in self._generated_emails:
            if e.message_id == message_id:
                return EmailMessage(
                    message_id=e.message_id,
                    thread_id=e.thread_id,
                    from_email=self.user_email,
                    from_name="Carlo",
                    to_email=e.to_email,
                    to_name=e.to_name,
                    subject=e.subject,
                    body=e.body,
                    date=e.sent_date,
                )
        raise ValueError(f"Message not found: {message_id}")

    def get_sent_log(self) -> list[dict]:
        """Get all emails 'sent' by the agent (for verification)."""
        return self._sent_log


# ─── Tool functions using mock provider ────────────────────────────

_mock: MockEmailProvider | None = None


def init_mock_provider(seed: int = 42, reply_probability: float = 0.3, email_count: int = 15):
    """Initialize mock provider and generate fake emails."""
    global _mock
    _mock = MockEmailProvider(seed=seed, reply_probability=reply_probability)
    _mock.generate_emails(count=email_count)
    logger.info(f"Mock provider initialized: {email_count} emails, {reply_probability:.0%} reply rate")


def _get_mock() -> MockEmailProvider:
    if _mock is None:
        raise RuntimeError("Mock provider not initialized")
    return _mock


def mock_watch_sent_folder(since_iso: str) -> str:
    """Get fake sent emails since the given timestamp. Returns realistic test data."""
    provider = _get_mock()
    since = datetime.fromisoformat(since_iso)
    emails = provider.watch_sent_folder(since)
    return json.dumps([{
        "message_id": e.message_id, "thread_id": e.thread_id,
        "to": f"{e.to_name} <{e.to_email}>", "subject": e.subject,
        "sent_date": e.sent_date.isoformat(), "body_preview": e.body[:300],
        "has_attachments": e.has_attachments,
    } for e in emails])


def mock_check_thread_for_reply(thread_id: str, after_iso: str) -> str:
    """Check if the mock thread received a reply."""
    provider = _get_mock()
    after = datetime.fromisoformat(after_iso)
    has_reply = provider.check_thread_for_reply(thread_id, after)
    return json.dumps({"thread_id": thread_id, "has_reply": has_reply})


def mock_send_follow_up_reply(thread_id: str, message_id: str, to: str, subject: str, body: str) -> str:
    """Log a mock follow-up email (does not really send)."""
    provider = _get_mock()
    new_id = provider.send_reply(thread_id, message_id, to, subject, body)
    return json.dumps({"status": "sent_mock", "new_message_id": new_id, "thread_id": thread_id})


def mock_read_email_message(message_id: str) -> str:
    """Read a mock email."""
    provider = _get_mock()
    msg = provider.read_message(message_id)
    return json.dumps({
        "message_id": msg.message_id, "thread_id": msg.thread_id,
        "from": f"{msg.from_name} <{msg.from_email}>",
        "to": f"{msg.to_name} <{msg.to_email}>",
        "subject": msg.subject, "body": msg.body, "date": msg.date.isoformat(),
    })
