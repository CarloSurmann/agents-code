"""
G2 — Customer Support Triage & First Response Agent (thin wiring file).

First agent built with the triage-and-learn paradigm:
  - ConfidenceGate: scores decisions, auto-approves trusted categories
  - ChannelHITL: gates send_support_reply behind human approval
  - FeedbackCapture: records every approve/edit/skip for learning
  - MonitorEngine: proactive business process monitoring

Hook order: [ConfidenceGate, ChannelHITL, FeedbackCapture]

Design: Ramp-inspired self-improving agent (2026-03-26).
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from agency import Agent
from agency.config import load_config, AgentConfig
from agency.skills import load_skills as load_skills
from agency.tracing import JSONTracer

# Shared tools
from agency.tools.support_kb import init_kb, search_kb
from agency.tools.email.mock import MockProvider

# Triage-and-learn hooks
from agency.hooks.confidence_gate import ConfidenceGate
from agency.hooks.hitl import ChannelHITL
from agency.hooks.feedback_capture import FeedbackCapture

# Feedback + monitors + tuning
from agency import feedback
from agency.monitors import MonitorEngine, load_monitors_from_config
from agency.tuning import generate_health_report, format_health_report

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# ─── Module-level mock provider for testing ────────────────────────────

_mock_provider: MockProvider | None = None


def _init_mock_provider():
    global _mock_provider
    _mock_provider = MockProvider()
    return _mock_provider


# ─── Tool functions (thin wrappers for the agent) ─────────────────────


def search_support_inbox() -> str:
    """Check for new incoming support emails. Returns a list of unread emails with sender, subject, and preview."""
    if _mock_provider is None:
        return json.dumps({"error": "Email provider not initialized"})

    emails = _mock_provider._inbox
    results = []
    for msg in emails:
        results.append({
            "message_id": msg.message_id,
            "from_email": msg.from_email,
            "from_name": msg.from_name,
            "subject": msg.subject,
            "body_preview": msg.body[:200],
            "date": msg.date.isoformat() if hasattr(msg, 'date') and msg.date else "",
        })
    return json.dumps({"count": len(results), "emails": results})


def read_support_email(message_id: str) -> str:
    """Read the full content of a support email by its message_id."""
    if _mock_provider is None:
        return json.dumps({"error": "Email provider not initialized"})

    try:
        msg = _mock_provider.read_message(message_id)
        return json.dumps({
            "message_id": msg.message_id,
            "from_email": msg.from_email,
            "from_name": msg.from_name,
            "subject": msg.subject,
            "body": msg.body,
            "thread_id": msg.thread_id,
        })
    except ValueError as e:
        return json.dumps({"error": str(e)})


def send_support_reply(to: str, subject: str, body: str, category: str, urgency: str) -> str:
    """Send a support response email. This tool is gated by HITL approval. Include the classification category and urgency for confidence routing."""
    if _mock_provider is None:
        return json.dumps({"error": "Email provider not initialized"})

    msg_id = _mock_provider.send_email(to=to, subject=f"Re: {subject}", body=body)
    logger.info(f"Support reply sent to {to}: {subject}")
    return json.dumps({"status": "sent", "message_id": msg_id, "to": to})


def log_ticket(from_email: str, subject: str, category: str, urgency: str, sentiment: str, kb_matched: bool, kb_query: str) -> str:
    """Log a support ticket for monitoring and analytics. Call this after classifying each email."""
    try:
        ticket_id = feedback.record_ticket(
            from_email=from_email,
            subject=subject,
            category=category,
            urgency=urgency,
            sentiment=sentiment,
            kb_matched=kb_matched,
            kb_query=kb_query,
        )
        return json.dumps({"status": "logged", "ticket_id": ticket_id})
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_monitors() -> str:
    """Run all business process monitors and return alerts. Call this after processing a batch of emails."""
    if not hasattr(run_monitors, "_engine"):
        return json.dumps({"alerts": [], "message": "No monitors configured"})

    alerts = run_monitors._engine.run_all()
    return run_monitors._engine.format_alerts(alerts)


def get_health_report() -> str:
    """Generate an agent health report showing approval rates, auto-promotion status, KB gaps, and drift detection."""
    try:
        report = generate_health_report(feedback._db_path or "")
        return format_health_report(report)
    except Exception as e:
        return f"Error generating health report: {e}"


# ─── System prompt builder ────────────────────────────────────────────


def build_system_prompt(config: AgentConfig, skills_content: str) -> str:
    """Build the full system prompt for the support triage agent."""
    now = datetime.now()

    return f"""You are a customer support triage agent for {config.extra.get('company_name', 'the company')}.

## Current Date and Time
Today is {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')}). Current time: {now.strftime('%H:%M')}.

## Your Workflow
For each support email, follow these steps in order:

1. **Read** the email using read_support_email
2. **Classify** it: determine category, urgency, and sentiment
3. **Log** the ticket using log_ticket (for monitoring)
4. **Search KB** using search_kb to find a matching FAQ answer
5. **Draft response** using the KB content + email context
6. **Send** via send_support_reply (triggers approval if not auto-approved)

After processing all emails:
7. **Run monitors** using run_monitors to check for anomalies
8. **Generate health report** using get_health_report

## Tools
- search_support_inbox: Get all incoming support emails
- read_support_email: Read full email content
- search_kb: Search FAQ knowledge base for answers
- send_support_reply: Send response (gated by approval)
- log_ticket: Record ticket for analytics
- run_monitors: Check business process monitors
- get_health_report: Generate agent health report

## Important Rules
1. ALWAYS log_ticket before drafting a response
2. ALWAYS search_kb before drafting — use KB content in your response
3. If search_kb returns no match, still draft a response but mention in log_ticket that kb_matched=false
4. Include the category in send_support_reply so confidence routing can work
5. For COMPLAINT + negative sentiment: recommend escalation, don't auto-respond
6. Keep responses under 150 words
7. Write in the customer's language

## Output Rules
- NEVER use tables or markdown headers — they don't render on messaging platforms
- Use bullet points, emojis, and short paragraphs
- Keep messages scannable on a phone screen

{skills_content}"""


# ─── Agent factory ────────────────────────────────────────────────────


def create_agent(config_path: str, channel=None) -> tuple:
    """Create and configure the customer support triage agent.

    Returns (agent, config, monitor_engine) tuple.
    """
    config = load_config(config_path)

    # Initialize providers
    _init_mock_provider()
    feedback.init_feedback(config.extra.get("feedback_db_path", "data/feedback.db"))
    init_kb(config.extra.get("kb_dir", str(BASE_DIR / "knowledge-base")))

    # Load skills
    skills_content = load_skills(["support_triage"])

    # Build system prompt
    system_prompt = build_system_prompt(config, skills_content)

    # Tools
    tools = [
        search_support_inbox,
        read_support_email,
        search_kb,
        send_support_reply,
        log_ticket,
        run_monitors,
        get_health_report,
    ]

    # Hooks — THE TRIAGE-AND-LEARN PARADIGM
    hooks = []
    if channel:
        # 1. Confidence Gate (runs first — sets metadata)
        confidence_gate = ConfidenceGate(
            gated_tools=["send_support_reply"],
            client_id=config.name,
            high_threshold=config.extra.get("confidence_high", 0.85),
            low_threshold=config.extra.get("confidence_low", 0.60),
            channel=channel,
        )

        # 2. HITL (reads metadata — skips if auto-approved)
        hitl = ChannelHITL(
            channel=channel,
            gated_tools=["send_support_reply"],
        )

        # 3. Feedback Capture (runs after — records everything)
        feedback_hook = FeedbackCapture(
            client_id=config.name,
            agent_name="support-triage",
            auto_promote_threshold=config.extra.get("auto_promote_streak", 20),
        )

        hooks = [confidence_gate, hitl, feedback_hook]  # ORDER MATTERS

    # Monitors
    monitor_defs = load_monitors_from_config(config.extra.get("monitors", []))
    monitor_engine = MonitorEngine(
        monitors=monitor_defs,
        db_path=config.extra.get("feedback_db_path", "data/feedback.db"),
    )
    # Attach to the run_monitors tool function
    run_monitors._engine = monitor_engine

    # Tracer
    tracer = JSONTracer()

    agent = Agent(
        name="support-triage",
        model=config.model,
        system_prompt=system_prompt,
        tools=tools,
        hooks=hooks,
        tracer=tracer,
        max_iterations=30,
    )

    return agent, config, monitor_engine
