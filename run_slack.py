#!/usr/bin/env python3
"""
Slack Bot Runner — Starts the conversational follow-up agent in Slack.

Runs a single process with:
- Slack Bolt app (Socket Mode for dev, HTTP for production)
- APScheduler cron (scan/check/followup every N hours)
- Agent factory (creates fresh Agent instances per request)

Usage:
    # Start the Slack bot (Socket Mode):
    python run_slack.py --config deployments/dev-slack.yaml

    # With verbose logging:
    python run_slack.py --config deployments/dev-slack.yaml -v

    # With mock email (no real Gmail):
    python run_slack.py --config deployments/dev-slack.yaml --mock
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

CODE_DIR = Path(__file__).parent
sys.path.insert(0, str(CODE_DIR))

from dotenv import load_dotenv
load_dotenv(CODE_DIR / ".env")

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from agency import Agent, load_skills, load_config
from agency.config import AgentConfig
from agency.tracing import Tracer
from agency.tools.tracker import (
    init_tracker, create_tracked_item, get_pending_items,
    get_due_follow_ups, mark_response_received, mark_follow_up_sent,
    cancel_tracking, get_weekly_stats, is_already_tracked,
)
from agency.tools.classifier import classify_sent_email
from agency.hooks.hitl.slack import SlackHITL, create_slack_hitl_hook
from agency.hooks.logger import ToolLogger
from agency.slack.app import create_slack_app
from agency.slack.conversations import ConversationStore
from agents.email_follow_up import build_system_prompt


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("slack_bolt").setLevel(logging.INFO)
    logging.getLogger("slack_sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Email Follow-Up Agent — Slack Bot")
    parser.add_argument("--config", required=True, help="Path to deployment YAML config")
    parser.add_argument("--mock", action="store_true", help="Use mock email provider (no real Gmail)")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-cron", action="store_true", help="Disable scheduled cron jobs")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("slack_runner")

    # ─── Load config ───────────────────────────────────────────────
    config = load_config(args.config)
    logger.info(f"Config loaded: {config.client_name}")

    # ─── Initialize providers ──────────────────────────────────────
    if args.mock:
        from agency.tools.email.mock import (
            init_mock_provider,
            mock_watch_sent_folder, mock_check_thread_for_reply,
            mock_send_follow_up_reply, mock_read_email_message,
        )
        init_mock_provider(seed=42, reply_probability=0.3, email_count=15)
        email_tools = [mock_watch_sent_folder, mock_check_thread_for_reply,
                       mock_send_follow_up_reply, mock_read_email_message]
        send_tool_name = "mock_send_follow_up_reply"
        logger.info("Using MOCK email provider")
    else:
        from agency.tools.email.gmail import (
            init_gmail_provider,
            watch_sent_folder, check_thread_for_reply,
            send_follow_up_reply, read_email_message,
        )
        init_gmail_provider(
            credentials_path=config.email.credentials_path,
            token_path=config.email.token_path or str(CODE_DIR / "secrets" / "gmail_token.json"),
            user_email=config.email.user_email,
        )
        email_tools = [watch_sent_folder, check_thread_for_reply,
                       send_follow_up_reply, read_email_message]
        send_tool_name = "send_follow_up_reply"
        logger.info(f"Using Gmail provider: {config.email.user_email}")

    init_tracker(config.storage.db_path)

    # ─── Load skills + build system prompt ─────────────────────────
    skills_content = load_skills(
        ["agency/skills/classify_sent_email.md", "agency/skills/draft_follow_up.md"],
        base_dir=str(CODE_DIR),
    )
    system_prompt = build_system_prompt(config, skills_content)

    # ─── Setup Slack ───────────────────────────────────────────────
    slack_client = WebClient(token=config.hitl.slack_bot_token)
    slack_hitl = SlackHITL(
        client=slack_client,
        channel_id=config.hitl.slack_channel_id,
        timeout_seconds=3600,
    )
    conversations = ConversationStore(max_messages=10)

    # ─── Agent factory ─────────────────────────────────────────────
    all_tools = email_tools + [
        classify_sent_email,
        create_tracked_item, get_pending_items, get_due_follow_ups,
        mark_response_received, mark_follow_up_sent, cancel_tracking,
        get_weekly_stats, is_already_tracked,
    ]

    tool_logger = ToolLogger(log_file=str(CODE_DIR / "data" / "agent_activity.log"))

    def agent_factory() -> Agent:
        """Create a fresh Agent for each request (DM or cron)."""
        tracer = Tracer(model=config.model, config_name=config.client_name)
        return Agent(
            name="email-follow-up-slack",
            model=config.model,
            system_prompt=system_prompt,
            tools=all_tools,
            hooks={
                "pre_tool_use": {
                    send_tool_name: create_slack_hitl_hook(slack_hitl),
                },
                "post_tool_use": {
                    "*": tool_logger,
                },
            },
            max_iterations=30,
            tracer=tracer,
        )

    # ─── Create Bolt app ──────────────────────────────────────────
    bolt_app = App(token=config.hitl.slack_bot_token)
    create_slack_app(
        bolt_app=bolt_app,
        agent_factory=agent_factory,
        slack_hitl=slack_hitl,
        conversation_store=conversations,
    )

    # ─── Setup cron scheduler ──────────────────────────────────────
    if not args.no_cron:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()

        def cron_cycle():
            """Run the full scan/check/followup cycle."""
            logger.info("CRON: Starting follow-up cycle...")
            try:
                since = datetime.now() - timedelta(hours=config.check_interval_hours)
                schedule_json = json.dumps([
                    {"number": s.number, "day_offset": s.day_offset, "template": s.template}
                    for s in config.follow_up_schedule
                ])

                prompt = f"""Execute the FULL follow-up cycle.

PHASE 1 — SCAN:
Call {"mock_watch_sent_folder" if args.mock else "watch_sent_folder"} with since_iso="{since.isoformat()}".
For each email: is_already_tracked → classify_sent_email → create_tracked_item if should_track.
Use follow_up_schedule_json: {schedule_json}

PHASE 2 — CHECK:
Call get_pending_items. For each: {"mock_check_thread_for_reply" if args.mock else "check_thread_for_reply"}. Mark responses.

PHASE 3 — FOLLOW-UP:
Call get_due_follow_ups. Draft and send each.

Report a summary of what happened."""

                agent = agent_factory()
                result = agent.run(prompt)
                logger.info(f"CRON: Cycle complete. {result.iterations} iterations, {len(result.tool_calls_made)} tool calls.")

                # Post summary to Slack channel
                if result.text:
                    summary = result.text[:2000]  # Truncate for Slack
                    slack_hitl.send_notification(f"🔄 *Scheduled cycle complete*\n\n{summary}")

            except Exception as e:
                logger.error(f"CRON: Error in cycle: {e}")
                slack_hitl.send_notification(f"❌ *Cron error:* {str(e)[:200]}", emoji="❌")

        scheduler.add_job(
            cron_cycle,
            "interval",
            hours=config.check_interval_hours,
            id="followup_cycle",
            next_run_time=None,  # Don't run immediately on start
        )
        scheduler.start()
        logger.info(f"Cron scheduled: every {config.check_interval_hours} hours")

    # ─── Start ─────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"  EMAIL FOLLOW-UP AGENT — Slack Bot")
    print(f"  Client: {config.client_name}")
    print(f"  Model: {config.model}")
    print(f"  Email: {'MOCK' if args.mock else config.email.user_email}")
    print(f"  Channel: {config.hitl.slack_channel_id}")
    print(f"  Cron: {'OFF' if args.no_cron else f'every {config.check_interval_hours}h'}")
    print(f"{'═' * 60}")
    print(f"  Bot is running! DM it or @mention in a channel.")
    print(f"  Press Ctrl+C to stop.\n")

    # Start with Socket Mode (dev) or HTTP (production)
    if config.hitl.slack_app_token:
        handler = SocketModeHandler(bolt_app, config.hitl.slack_app_token)
        handler.start()  # Blocks
    else:
        bolt_app.start(port=3000)  # HTTP mode


if __name__ == "__main__":
    main()
