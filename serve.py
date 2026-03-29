"""serve.py — Universal entry point for running any customer's agent.

This is the PRODUCTION entry point. It reads a customer config, wires up the
right agent + channel + tools, and starts listening.

Usage:
    # Run a specific customer
    python serve.py --customer pizzeria-mario

    # Run with Docker (CUSTOMER_ID env var)
    docker run -e CUSTOMER_ID=pizzeria-mario ai-agency-agent

    # Interactive mode (for testing)
    python serve.py --customer pizzeria-mario --interactive

For quick dev testing without customer configs, use the old entry points:
    python server.py          # Telegram/WhatsApp AR bot
    python run.py             # CLI email follow-up
    python run_slack.py       # Slack email follow-up
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml
from dotenv import load_dotenv

from agency.agent import Agent
from agency.channels.base import Channel, IncomingMessage
from agency.hooks.hitl import ChannelHITL
from agency.tracing import JSONTracer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Project root
ROOT = Path(__file__).resolve().parent


# =============================================================================
# Config Loading
# =============================================================================

def load_customer_config(customer_id: str) -> dict:
    """Load a customer's config.yaml and .env secrets."""
    customer_dir = ROOT / "customers" / customer_id

    if not customer_dir.exists():
        available = [d.name for d in (ROOT / "customers").iterdir()
                     if d.is_dir() and not d.name.startswith("_")]
        raise FileNotFoundError(
            f"Customer '{customer_id}' not found in customers/.\n"
            f"Available: {available or 'none — use onboard.py to create one'}"
        )

    # Load .env (secrets)
    env_path = customer_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    else:
        logger.warning(f"No .env found for {customer_id} — using global env")

    # Load config.yaml
    config_path = customer_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"No config.yaml found in {customer_dir}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Inject computed paths
    config["_customer_dir"] = str(customer_dir)
    config["_customer_id"] = customer_id

    return config


# =============================================================================
# Channel Factory
# =============================================================================

def create_channel(config: dict) -> Channel:
    """Create the messaging channel from customer config."""
    channel_config = config.get("channel", {})
    channel_type = channel_config.get("type", "console")

    if channel_type == "whatsapp":
        from agency.channels.whatsapp import WhatsAppChannel
        return WhatsAppChannel()  # reads from env

    elif channel_type == "teams":
        from agency.channels.teams import TeamsChannel
        return TeamsChannel()  # reads from env

    elif channel_type == "telegram":
        from agency.channels.telegram import TelegramChannel
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        return TelegramChannel(allowed_chat_ids=[chat_id] if chat_id else None)

    elif channel_type == "slack":
        from agency.channels.slack import SlackChannel
        return SlackChannel()

    elif channel_type == "console":
        from agency.channels.console import ConsoleChannel
        return ConsoleChannel()

    else:
        raise ValueError(
            f"Unknown channel type: '{channel_type}'. "
            f"Use: whatsapp, teams, telegram, slack, console"
        )


# =============================================================================
# Tool Factory
# =============================================================================

def create_tools(config: dict) -> list:
    """Create the tool set based on customer config."""
    tools = []
    workflow = config.get("workflow", "ar-follow-up")

    # --- Accounting tools ---
    accounting = config.get("accounting", {})
    provider = accounting.get("provider", "none") if isinstance(accounting, dict) else accounting

    if provider == "fattureincloud":
        from agency.tools.fattureincloud import (
            get_overdue_invoices, check_payment_status, get_company_info,
        )
        tools.extend([get_overdue_invoices, check_payment_status, get_company_info])

    elif provider == "exact_online":
        from agency.tools.exact_online import (
            get_overdue_invoices, check_payment_status, get_customer_contacts,
        )
        tools.extend([get_overdue_invoices, check_payment_status, get_customer_contacts])

    # --- Email tools ---
    email_config = config.get("email", {})
    email_provider = email_config.get("provider", "none") if isinstance(email_config, dict) else email_config

    if email_provider == "gmail":
        from agency.tools.email.gmail import send_email, search_inbox, read_message
        tools.extend([send_email, search_inbox, read_message])

        # Email follow-up specific tools
        if workflow == "email-follow-up":
            from agency.tools.email.gmail import (
                watch_sent_folder, check_thread_for_reply,
                send_follow_up_reply, read_email_message,
            )
            from agency.tools.classifier import classify_sent_email
            tools.extend([
                watch_sent_folder, check_thread_for_reply,
                send_follow_up_reply, read_email_message,
                classify_sent_email,
            ])

    elif email_provider == "outlook":
        from agency.tools.email.outlook import send_email, search_inbox, read_message
        tools.extend([send_email, search_inbox, read_message])

    elif email_provider == "mock":
        from agency.tools.email.mock import send_email, search_inbox, read_message
        tools.extend([send_email, search_inbox, read_message])

    # --- Tracker tools (for email-follow-up workflow) ---
    if workflow == "email-follow-up":
        from agency.tools.tracker import (
            create_tracked_item, get_pending_items, get_due_follow_ups,
            mark_response_received, mark_follow_up_sent, cancel_tracking,
            get_weekly_stats, is_already_tracked, init_tracker,
        )
        customer_dir = config.get("_customer_dir", ".")
        storage = config.get("storage", {})
        db_path = storage.get("db_path", "tracker.db")
        if not os.path.isabs(db_path):
            db_path = os.path.join(customer_dir, db_path)
        init_tracker(db_path)

        tools.extend([
            create_tracked_item, get_pending_items, get_due_follow_ups,
            mark_response_received, mark_follow_up_sent, cancel_tracking,
            get_weekly_stats, is_already_tracked,
        ])

    # --- Memory tool ---
    memory_file = config.get("memory_file", "memory.md")
    customer_dir = config.get("_customer_dir", ".")
    memory_path = os.path.join(customer_dir, memory_file)

    from agency.tools.memory import create_memory_tools
    mem_tools = create_memory_tools(memory_path)
    tools.extend(mem_tools)

    return tools


# =============================================================================
# System Prompt Builder
# =============================================================================

def build_system_prompt(config: dict) -> str:
    """Build the full system prompt from workflow base + customer overrides."""
    workflow = config.get("workflow", "ar-follow-up")

    # --- Base prompt per workflow ---
    if workflow == "ar-follow-up":
        base_prompt = _ar_follow_up_prompt()
    elif workflow == "email-follow-up":
        base_prompt = _email_follow_up_prompt(config)
    else:
        base_prompt = f"You are a helpful assistant for the {workflow} workflow."

    # --- Inject customer identity ---
    company_name = config.get("company_name", "the company")
    contact_name = config.get("contact_name", "the user")
    language = config.get("language", "English")
    voice = config.get("voice", {})
    tone = voice.get("tone", "professional, warm, direct")
    company_context = voice.get("company_context", "")

    now = datetime.now()

    identity_block = f"""
## Customer
Company: {company_name}
Contact: {contact_name}
Language: {language}
Tone: {tone}
{"Company context: " + company_context if company_context else ""}

## Current Date and Time
Today is {now.strftime('%A, %B %d, %Y')} ({now.strftime('%Y-%m-%d')}). Current time: {now.strftime('%H:%M')}.
Use this for any date calculations — never ask the user for today's date.
"""

    # --- Custom instructions ---
    custom = config.get("custom_instructions", "").strip()
    custom_block = f"\n## Customer-Specific Instructions\n{custom}\n" if custom else ""

    # --- Memory ---
    memory_file = config.get("memory_file", "memory.md")
    customer_dir = config.get("_customer_dir", ".")
    memory_path = os.path.join(customer_dir, memory_file)
    memory_block = ""
    if os.path.exists(memory_path):
        with open(memory_path, encoding="utf-8") as f:
            memory_content = f.read().strip()
        if memory_content:
            memory_block = f"\n## Agent Memory\nThese are facts you've learned from previous conversations:\n{memory_content}\n"

    # --- Communication rules (universal) ---
    comm_rules = """
## Communication Rules
- ABSOLUTELY NO TABLES — no pipes (|), no dashes (---), no grid formatting. Tables are broken on WhatsApp/Telegram.
- List items as bullet points with emojis instead.
- NEVER use markdown headers (#) — use bold (*text*) or emojis instead.
- Keep messages scannable on a phone screen.
- Be concise — this is a chat, not a report.
"""

    return base_prompt + identity_block + custom_block + memory_block + comm_rules


def _ar_follow_up_prompt() -> str:
    """Base prompt for AR follow-up workflow."""
    return """You are an Accounts Receivable assistant. You help the business owner manage overdue invoices by having a conversation with them.

Your workflow:
1. When asked, fetch overdue invoices using the get_overdue_invoices tool
2. Present a clear briefing: which invoices are overdue, how long, any context
3. Ask the owner what they'd like to do about each one
4. Draft chase emails based on their instructions
5. Show the draft to the owner for approval before sending
6. Send each email only after explicit approval

Finding the right email address:
- Each invoice includes a customer_email field from the accounting system
- If customer_email is empty, search the inbox for previous correspondence
- ALWAYS confirm the email address with the owner before sending
- If nothing found, ask: "I don't have a contact for [company] — who should I reach out to?"

Chase email guidelines:
- 1-6 days overdue: Friendly reminder. Casual tone.
- 7-13 days: Firm but warm. Ask for a payment date.
- 14-29 days: Formal. Reference previous reminders.
- 30+ days: Final notice. Serious but professional.

Rules:
- Never threaten legal action (that's a human decision)
- Always include invoice number, amount, and due date
- Respect the customer relationship
- If the owner says skip, skip without question

When presenting invoices, format as bullet points grouped by urgency, like:
  🔴 Fattura 4/2026 — Verde Distribuzione SRL — €7.442 — scaduta da 33gg
"""


def _email_follow_up_prompt(config: dict) -> str:
    """Base prompt for email follow-up workflow."""
    # Build schedule description
    follow_up = config.get("follow_up", {})
    schedule = follow_up.get("schedule", [])
    schedule_desc = "\n".join(
        f"  - Follow-up #{s['number']}: Day {s['day_offset']} ({s['template']})"
        for s in schedule
    ) or "  - Default schedule: Day 3, 7, 14, 21"

    return f"""You are an AI email follow-up agent. Your job is to execute a structured follow-up workflow.

## Your Tools
- watch_sent_folder: Check for new outbound emails
- classify_sent_email: Classify whether an email needs follow-up tracking
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

## Important Rules
1. NEVER send a follow-up without the send_follow_up_reply tool (triggers human approval).
2. Always check is_already_tracked before creating a new tracked item.
3. ALWAYS use classify_sent_email tool — don't classify emails yourself.
4. Only create_tracked_item if classify returns should_track=true.
5. Keep follow-up emails under 100 words. Sound human, not robotic.
"""


# =============================================================================
# Main
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Run a customer's AI agent")
    parser.add_argument(
        "--customer", "-c",
        default=os.environ.get("CUSTOMER_ID"),
        help="Customer ID (folder name in customers/). Or set CUSTOMER_ID env var.",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="Run in interactive chat mode (ignores channel config, uses console).",
    )
    args = parser.parse_args()

    if not args.customer:
        # List available customers
        customers_dir = ROOT / "customers"
        available = [d.name for d in customers_dir.iterdir()
                     if d.is_dir() and not d.name.startswith("_")]
        print("Usage: python serve.py --customer <customer-id>\n")
        print(f"Available customers: {available or 'none'}")
        print("Run: python onboard.py  to create a new customer")
        sys.exit(1)

    # Load config
    config = load_customer_config(args.customer)
    customer_id = config["_customer_id"]
    company_name = config.get("company_name", customer_id)

    # Override channel to console for interactive mode
    if args.interactive:
        config.setdefault("channel", {})["type"] = "console"

    # Create channel
    channel = create_channel(config)

    # Create tools
    tools = create_tools(config)

    # Create HITL hook
    hooks = []
    hitl_config = config.get("hitl", {})
    if hitl_config.get("enabled", True):
        gated = hitl_config.get("gated_tools", ["send_email"])
        hitl = ChannelHITL(channel=channel, gated_tools=gated)
        hooks.append(hitl)

    # Build system prompt
    system_prompt = build_system_prompt(config)

    # Create tracer
    tracer = JSONTracer()

    # Build agent
    model = config.get("model", "claude-sonnet-4-6")
    agent = Agent(
        name=f"{config.get('workflow', 'agent')}-{customer_id}",
        system_prompt=system_prompt,
        model=model,
        tools=tools,
        hooks=hooks,
        tracer=tracer,
    )

    # --- Start ---
    logger.info(
        f"Starting agent for {company_name} "
        f"(workflow: {config.get('workflow')}, "
        f"channel: {config.get('channel', {}).get('type')}, "
        f"model: {model})"
    )

    async def handle_message(msg: IncomingMessage):
        logger.info(f"[{customer_id}] Received from {msg.sender_name}: {msg.text}")
        async with channel.typing():
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, agent.run, msg.text)
        if result.output:
            await channel.send_message(result.output)
        if result.trace_file:
            logger.info(f"[{customer_id}] Trace: {result.trace_file}")

    await channel.start(handle_message)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info(f"[{customer_id}] Shutting down...")
        await channel.stop()


if __name__ == "__main__":
    asyncio.run(main())
