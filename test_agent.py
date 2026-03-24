#!/usr/bin/env python3
"""
Test harness for the Email Follow-Up Agent.

Runs the full agent pipeline with:
- MOCK Gmail (no real emails sent)
- REAL Claude API calls (measures actual tokens)
- FULL tracing (every API call, tool call, classification)
- Auto-HITL (approves everything — no terminal input needed)

Usage:
    # Quick test with Haiku (cheapest, ~$0.01 per run):
    python test_agent.py --model claude-haiku-4-5

    # Test with Sonnet:
    python test_agent.py --model claude-sonnet-4-6

    # Test with Opus (full quality):
    python test_agent.py --model claude-opus-4-6

    # Control email count and reply rate:
    python test_agent.py --emails 20 --reply-rate 0.3

    # Save trace to file:
    python test_agent.py --save-trace

    # Run specific phase only:
    python test_agent.py --phase scan
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(".env")

from agency.agent import Agent, python_function_to_tool_schema
from agency.config import AgentConfig, VoiceConfig, FollowUpStep
from agency.skills import load_skills
from agency.tracing import Tracer
from agency.tools.email.mock import (
    init_mock_provider, _get_mock,
    mock_watch_sent_folder, mock_check_thread_for_reply,
    mock_send_follow_up_reply, mock_read_email_message,
)
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
        level=level, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def auto_approve_hook(tool_name: str, tool_input: dict) -> dict | None:
    """Auto-approve everything — no terminal input needed for testing."""
    if tool_name == "mock_send_follow_up_reply":
        print(f"  [AUTO-APPROVE] Follow-up to {tool_input.get('to', '?')}: {tool_input.get('subject', '?')}")
        return None  # Proceed
    return None


def main():
    parser = argparse.ArgumentParser(description="Test Email Follow-Up Agent")
    parser.add_argument("--model", default="claude-haiku-4-5",
                        help="Model to test with (default: claude-haiku-4-5 — cheapest)")
    parser.add_argument("--emails", type=int, default=10,
                        help="Number of fake emails to generate (default: 10)")
    parser.add_argument("--reply-rate", type=float, default=0.3,
                        help="Probability that a tracked email has a reply (default: 0.3)")
    parser.add_argument("--phase", choices=["scan", "check", "followup", "full"], default="full")
    parser.add_argument("--save-trace", action="store_true", help="Save trace JSON to traces/")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # ─── Initialize ────────────────────────────────────────────────
    print(f"\n{'═' * 70}")
    print(f"  EMAIL FOLLOW-UP AGENT — TEST HARNESS")
    print(f"  Model: {args.model}")
    print(f"  Emails: {args.emails}  |  Reply rate: {args.reply_rate:.0%}  |  Seed: {args.seed}")
    print(f"{'═' * 70}")

    # Mock email provider
    init_mock_provider(seed=args.seed, reply_probability=args.reply_rate, email_count=args.emails)
    mock = _get_mock()

    # Tracker (use temp DB)
    db_path = f"./data/test_{args.seed}.db"
    init_tracker(db_path)

    # Config
    config = AgentConfig(
        client_name="Test Harness", model=args.model,
        voice=VoiceConfig(
            user_name="Carlo", company_name="AI Agency",
            tone="professional, warm, direct", language="English",
            company_context="AI agency helping European SMBs automate workflows.",
        ),
        follow_up_schedule=[
            FollowUpStep(1, 3, "gentle_check_in"), FollowUpStep(2, 7, "add_value"),
            FollowUpStep(3, 14, "create_urgency"), FollowUpStep(4, 21, "graceful_close"),
        ],
    )

    # Skills
    skills_content = load_skills(
        ["agency/skills/classify_sent_email.md", "agency/skills/draft_follow_up.md"],
        base_dir=".",
    )
    system_prompt = build_system_prompt(config, skills_content)

    # Tracer
    tracer = Tracer(model=args.model, phase=args.phase, config_name="test-harness")

    # Tools (mock email + real tracker + real classifier)
    tools = [
        mock_watch_sent_folder,
        classify_sent_email,
        mock_check_thread_for_reply,
        mock_send_follow_up_reply,
        mock_read_email_message,
        create_tracked_item,
        get_pending_items,
        get_due_follow_ups,
        mark_response_received,
        mark_follow_up_sent,
        cancel_tracking,
        get_weekly_stats,
        is_already_tracked,
    ]

    # Hooks
    hooks = {
        "pre_tool_use": {
            "mock_send_follow_up_reply": auto_approve_hook,
        },
        "post_tool_use": {
            "*": tracer.tool_hook(),
        },
    }

    agent = Agent(
        name="email-follow-up-test",
        model=args.model,
        system_prompt=system_prompt,
        tools=tools,
        hooks=hooks,
        max_iterations=50,
        tracer=tracer,
    )

    # ─── Build phase prompt ────────────────────────────────────────
    since = datetime.now() - timedelta(days=7)
    since_iso = since.isoformat()
    schedule_json = json.dumps([
        {"number": s.number, "day_offset": s.day_offset, "template": s.template}
        for s in config.follow_up_schedule
    ])

    prompts = {
        "scan": f"""Execute PHASE 1 — SCAN.
1. Call mock_watch_sent_folder with since_iso="{since_iso}"
2. For each email: call is_already_tracked, then classify_sent_email, then create_tracked_item if should_track=true
   Use follow_up_schedule_json: {schedule_json}
3. Report summary: how many scanned, tracked, skipped.""",

        "check": """Execute PHASE 2 — CHECK.
1. Call get_pending_items
2. For each: call mock_check_thread_for_reply with thread_id and sent_date
3. If reply found: mark_response_received
4. Report summary.""",

        "followup": """Execute PHASE 3 — FOLLOW-UP.
1. Call get_due_follow_ups
2. For each due: draft a follow-up using the Draft Follow-Up skill, then call mock_send_follow_up_reply
3. After sending: mark_follow_up_sent
4. Report summary.""",

        "full": f"""Execute ALL phases in order.

PHASE 1 — SCAN:
Call mock_watch_sent_folder with since_iso="{since_iso}".
For each email: is_already_tracked → classify_sent_email → create_tracked_item if should_track.
Use follow_up_schedule_json: {schedule_json}

PHASE 2 — CHECK:
Call get_pending_items. For each: mock_check_thread_for_reply. Mark responses.

PHASE 3 — FOLLOW-UP:
Call get_due_follow_ups. Draft and send each. Mark sent.

Report a complete summary of what happened in each phase.""",
    }

    prompt = prompts[args.phase]

    # ─── Run ───────────────────────────────────────────────────────
    print(f"\n  Running phase: {args.phase.upper()}...")
    print(f"  (Agent is thinking...)\n")

    start = time.time()
    result = agent.run(prompt)
    elapsed = time.time() - start

    # ─── Results ───────────────────────────────────────────────────
    print(f"\n{'─' * 70}")
    print(f"  AGENT OUTPUT:")
    print(f"{'─' * 70}")
    print(result.text)

    # ─── Trace ─────────────────────────────────────────────────────
    tracer.print_summary()

    print(f"  Wall time: {elapsed:.1f}s")
    print(f"  Agent iterations: {result.iterations}")

    # Mock provider stats
    sent_log = mock.get_sent_log()
    if sent_log:
        print(f"\n  EMAILS 'SENT' BY AGENT ({len(sent_log)}):")
        for s in sent_log:
            print(f"    → {s['to']}: {s['subject']}")
            print(f"      Body: {s['body'][:100]}...")

    # ─── Save trace ────────────────────────────────────────────────
    if args.save_trace:
        trace_path = f"traces/{tracer._trace.run_id}.json"
        tracer.save(trace_path)
        print(f"\n  Trace saved: {trace_path}")

    # Cleanup test DB
    import os
    try:
        os.remove(db_path)
    except Exception:
        pass


if __name__ == "__main__":
    main()
