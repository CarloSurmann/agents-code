"""
A3 — Email Follow-Up Agent (thin wiring file).

This file wires shared agency components into the email follow-up workflow.
All intelligence lives in agency/. This file is just configuration.

Design: Giovanni's three-layer architecture (2026-03-24).
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from agency import Agent, load_skills, load_config
from agency.config import AgentConfig
from agency.tools.email.gmail import (
    init_gmail_provider,
    watch_sent_folder,
    check_thread_for_reply,
    send_follow_up_reply,
    read_email_message,
)
from agency.tools.tracker import (
    init_tracker,
    create_tracked_item,
    get_pending_items,
    get_due_follow_ups,
    mark_response_received,
    mark_follow_up_sent,
    cancel_tracking,
    get_weekly_stats,
    is_already_tracked,
)
from agency.tools.classifier import classify_sent_email
from agency.hooks.hitl.console import ConsoleHITL, create_console_hitl_hook
from agency.hooks.logger import ToolLogger

logger = logging.getLogger(__name__)

# Base directory for resolving relative paths
BASE_DIR = Path(__file__).parent.parent


def build_system_prompt(config: AgentConfig, skills_content: str) -> str:
    """Build the full system prompt for the email follow-up agent."""

    schedule_desc = "\n".join(
        f"  - Follow-up #{s.number}: Day {s.day_offset} ({s.template})"
        for s in config.follow_up_schedule
    )

    now = datetime.now()

    return f"""You are an AI email follow-up agent working for {config.voice.user_name or 'the user'} at {config.voice.company_name or 'their company'}.

## Current Date and Time
Today is {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')}). Current time: {now.strftime('%H:%M')} local time.
Use this for any date calculations — never ask the user for today's date.

Your job is to execute a structured follow-up workflow in phases. Follow the instructions precisely.

## Your Tools
- watch_sent_folder: Check for new outbound emails
- classify_sent_email: Classify whether an email needs follow-up tracking (uses rules + LLM)
- check_thread_for_reply: See if someone replied to a tracked email
- send_follow_up_reply: Send a follow-up email (as a reply in the original thread)
- read_email_message: Read the full content of an email
- create_tracked_item: Start tracking a sent email for follow-up
- get_pending_items: Get all emails awaiting responses
- get_due_follow_ups: Get follow-ups that are due right now
- mark_response_received: Mark that someone replied
- mark_follow_up_sent: Record that a follow-up was sent
- cancel_tracking: Stop tracking an email
- get_weekly_stats: Get summary statistics
- is_already_tracked: Check if a thread is already tracked

## Follow-Up Schedule
{schedule_desc}

## Voice and Tone
{config.voice.tone}
Language: {config.voice.language}
{f"Company context: {config.voice.company_context}" if config.voice.company_context else ""}

## Important Rules
1. NEVER send a follow-up without going through the send_follow_up_reply tool (which triggers human approval).
2. Always check is_already_tracked before creating a new tracked item.
3. ALWAYS use the classify_sent_email tool to classify emails. Do NOT classify emails yourself — the tool has a two-layer system (rules + dedicated LLM call) that is more accurate. Trust its output.
4. Only create_tracked_item if classify_sent_email returns should_track=true. Use the context_summary from the classification result.
5. When checking for replies, if a reply is found, immediately mark_response_received and move on.
6. When drafting follow-ups, use the context_summary from the tracked item to write relevant, personalized content.
7. Keep follow-up emails under 100 words. Sound human, not robotic.

{skills_content}"""


def create_agent(config_path: str) -> tuple[Agent, AgentConfig]:
    """Create and configure the email follow-up agent."""

    config = load_config(config_path)

    # Initialize providers
    init_gmail_provider(
        credentials_path=config.email.credentials_path,
        token_path=config.email.token_path or str(BASE_DIR / "secrets" / "gmail_token.json"),
        user_email=config.email.user_email,
    )
    init_tracker(config.storage.db_path)

    # Load skills
    skills_content = load_skills(
        [
            "agency/skills/classify_sent_email.md",
            "agency/skills/draft_follow_up.md",
        ],
        base_dir=str(BASE_DIR),
    )

    # Build system prompt
    system_prompt = build_system_prompt(config, skills_content)

    # Setup tools
    tools = [
        watch_sent_folder,
        classify_sent_email,
        check_thread_for_reply,
        send_follow_up_reply,
        read_email_message,
        create_tracked_item,
        get_pending_items,
        get_due_follow_ups,
        mark_response_received,
        mark_follow_up_sent,
        cancel_tracking,
        get_weekly_stats,
        is_already_tracked,
    ]

    # Setup hooks
    hitl = ConsoleHITL()
    tool_logger = ToolLogger(log_file=str(BASE_DIR / "data" / "agent_activity.log"))

    hooks = {
        "pre_tool_use": {
            "send_follow_up_reply": create_console_hitl_hook(hitl),
        },
        "post_tool_use": {
            "*": tool_logger,
        },
    }

    agent = Agent(
        name="email-follow-up",
        model=config.model,
        system_prompt=system_prompt,
        tools=tools,
        hooks=hooks,
        max_iterations=30,
    )

    return agent, config
