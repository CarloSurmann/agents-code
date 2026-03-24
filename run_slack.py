#!/usr/bin/env python3
"""
Slack Bot Runner — Starts the conversational follow-up agent in Slack.

Runs a single process with:
- SlackChannel (Socket Mode for dev, HTTP for production)
- APScheduler cron (scan/check/followup every N hours)
- Agent factory (creates fresh Agent instances per request)

Updated: 2026-03-24 — uses unified Channel interface + ChannelHITL hooks.

Usage:
    python run_slack.py --config deployments/dev-slack.yaml
    python run_slack.py --config deployments/dev-slack.yaml --mock
    python run_slack.py --config deployments/dev-slack.yaml --mock --no-cron -v
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

CODE_DIR = Path(__file__).parent
sys.path.insert(0, str(CODE_DIR))

from dotenv import load_dotenv
load_dotenv(CODE_DIR / ".env")

from agency import Agent, load_skills, load_config
from agency.channels.slack import SlackChannel
from agency.hooks.hitl import ChannelHITL
from agency.tracing import JSONTracer
from agency.tools.tracker import (
    init_tracker, create_tracked_item, get_pending_items,
    get_due_follow_ups, mark_response_received, mark_follow_up_sent,
    cancel_tracking, get_weekly_stats, is_already_tracked,
)
from agency.tools.classifier import classify_sent_email
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

    # ─── Initialize email provider ─────────────────────────────────
    if args.mock:
        from agency.tools.email.mock import (
            init_mock_provider,
            mock_watch_sent_folder, mock_check_thread_for_reply,
            mock_send_follow_up_reply, mock_read_email_message,
        )
        init_mock_provider(seed=42, reply_probability=0.3, email_count=15)
        email_tools = [mock_watch_sent_folder, mock_check_thread_for_reply,
                       mock_send_follow_up_reply, mock_read_email_message]
        send_tool_names = ["mock_send_follow_up_reply"]
        watch_tool = "mock_watch_sent_folder"
        check_tool = "mock_check_thread_for_reply"
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
        send_tool_names = ["send_follow_up_reply"]
        watch_tool = "watch_sent_folder"
        check_tool = "check_thread_for_reply"
        logger.info(f"Using Gmail provider: {config.email.user_email}")

    init_tracker(config.storage.db_path)

    # ─── Load skills + build system prompt ─────────────────────────
    skills_content = load_skills(
        ["agency/skills/classify_sent_email.md", "agency/skills/draft_follow_up.md"],
        base_dir=str(CODE_DIR),
    )
    system_prompt = build_system_prompt(config, skills_content)

    # ─── Setup Slack Channel ──────────────────────────────────────
    slack_channel = SlackChannel(
        bot_token=config.hitl.slack_bot_token,
        app_token=config.hitl.slack_app_token,
    )

    # ─── All tools ─────────────────────────────────────────────────
    all_tools = email_tools + [
        classify_sent_email,
        create_tracked_item, get_pending_items, get_due_follow_ups,
        mark_response_received, mark_follow_up_sent, cancel_tracking,
        get_weekly_stats, is_already_tracked,
    ]

    # ─── Agent factory ─────────────────────────────────────────────
    def agent_factory() -> Agent:
        """Create a fresh Agent for each request (DM or cron)."""
        return Agent(
            name="email-follow-up-slack",
            model=config.model,
            system_prompt=system_prompt,
            tools=all_tools,
            hooks=[
                ChannelHITL(channel=slack_channel, gated_tools=send_tool_names),
            ],
            max_iterations=30,
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
Call {watch_tool} with since_iso="{since.isoformat()}".
For each email: is_already_tracked → classify_sent_email → create_tracked_item if should_track.
Use follow_up_schedule_json: {schedule_json}

PHASE 2 — CHECK:
Call get_pending_items. For each: {check_tool}. Mark responses.

PHASE 3 — FOLLOW-UP:
Call get_due_follow_ups. Draft and send each.

Report a summary of what happened."""

                agent = agent_factory()
                result = agent.run(prompt)
                logger.info(f"CRON: Cycle complete. {result.iterations} iterations.")

                # Post summary to Slack channel
                if result.output:
                    summary = result.output[:2000]
                    asyncio.run(slack_channel.send_message(f"🔄 *Scheduled cycle complete*\n\n{summary}"))

            except Exception as e:
                logger.error(f"CRON: Error in cycle: {e}")
                try:
                    asyncio.run(slack_channel.send_message(f"❌ *Cron error:* {str(e)[:200]}"))
                except Exception:
                    pass

        scheduler.add_job(
            cron_cycle,
            "interval",
            hours=config.check_interval_hours,
            id="followup_cycle",
            next_run_time=None,
        )
        scheduler.start()
        logger.info(f"Cron scheduled: every {config.check_interval_hours} hours")

    # ─── Message handler ──────────────────────────────────────────
    async def on_message(msg):
        """Handle incoming Slack messages (DMs and @mentions)."""
        logger.info(f"Message from {msg.sender_name}: {msg.text[:50]}...")
        agent = agent_factory()

        async with slack_channel.typing():
            result = agent.run(msg.text)

        response = result.output or "Done."
        await slack_channel.send_message(response)

    # ─── Start ─────────────────────────────────────────────────────
    print(f"\n{'═' * 60}")
    print(f"  EMAIL FOLLOW-UP AGENT — Slack Bot")
    print(f"  Client: {config.client_name}")
    print(f"  Model: {config.model}")
    print(f"  Email: {'MOCK' if args.mock else config.email.user_email}")
    print(f"  Cron: {'OFF' if args.no_cron else f'every {config.check_interval_hours}h'}")
    print(f"{'═' * 60}")
    print(f"  Bot is running! DM it or @mention in a channel.")
    print(f"  Press Ctrl+C to stop.\n")

    # Start the Slack channel (blocks)
    asyncio.run(slack_channel.start(on_message))


if __name__ == "__main__":
    main()
