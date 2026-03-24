#!/usr/bin/env python3
"""
Email Follow-Up Agent — Runner Script.

Usage:
    # Run full cycle (scan + check + follow-up):
    python run.py --config deployments/dev.yaml

    # Run specific phase:
    python run.py --config deployments/dev.yaml --phase scan
    python run.py --config deployments/dev.yaml --phase check
    python run.py --config deployments/dev.yaml --phase followup
    python run.py --config deployments/dev.yaml --phase stats

    # Interactive mode (ask the agent anything):
    python run.py --config deployments/dev.yaml --interactive
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add code/ to Python path
CODE_DIR = Path(__file__).parent
sys.path.insert(0, str(CODE_DIR))

# Load .env file BEFORE any imports that need env vars
from dotenv import load_dotenv
load_dotenv(CODE_DIR / ".env")

from agents.email_follow_up import create_agent


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy libraries
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


PHASE_PROMPTS = {
    "scan": """Execute PHASE 1 — SCAN for new sent emails.

1. Call watch_sent_folder with since_iso set to 24 hours ago: {since_iso}
2. For each email found:
   a. Call is_already_tracked with the thread_id — skip if already tracked
   b. Call classify_sent_email with the email's subject, body, to_email, to_name, from_email, and has_attachments
   c. If the classifier returns should_track=true, call create_tracked_item using:
      - The context_summary and item_type FROM THE CLASSIFIER RESULT (do not make up your own)
      - This follow-up schedule JSON: {schedule_json}
   d. If should_track=false, skip it and note why (the classifier provides the reason)
3. Report a summary: how many emails scanned, how many tracked, how many skipped and why.""",

    "check": """Execute PHASE 2 — CHECK for responses to tracked emails.

1. Call get_pending_items to get all emails awaiting responses
2. For each pending item:
   a. Call check_thread_for_reply with the thread_id and sent_date
   b. If a reply was found, call mark_response_received
3. Report which items received responses and which are still waiting.""",

    "followup": """Execute PHASE 3 — Send due FOLLOW-UPS.

1. Call get_due_follow_ups to find follow-ups that are due
2. For each due follow-up:
   a. Draft a follow-up email using the Draft Follow-Up Email skill
   b. Use the context_summary, follow_up_number, and days_elapsed to write the draft
   c. Call send_follow_up_reply with the drafted email
   d. After sending, call mark_follow_up_sent
3. Report what follow-ups were sent.""",

    "stats": """Get the weekly statistics report.

1. Call get_weekly_stats
2. Present the results in a clear, formatted summary.""",

    "full": """Execute the FULL follow-up cycle (all phases in order).

PHASE 1 — SCAN:
1. Call watch_sent_folder with since_iso = {since_iso}
2. For each email: is_already_tracked → classify_sent_email → create_tracked_item if should_track=true
   Use this follow-up schedule JSON: {schedule_json}

PHASE 2 — CHECK:
3. Call get_pending_items
4. For each, check_thread_for_reply. Mark responses received.

PHASE 3 — FOLLOW-UP:
5. Call get_due_follow_ups
6. Draft and send each due follow-up.

Report a summary of everything that happened.""",
}


def main():
    parser = argparse.ArgumentParser(description="Email Follow-Up Agent")
    parser.add_argument("--config", required=True, help="Path to deployment YAML config")
    parser.add_argument("--phase", choices=["scan", "check", "followup", "stats", "full"],
                        default="full", help="Which phase to run (default: full)")
    parser.add_argument("--since-hours", type=int, default=24,
                        help="How many hours back to scan for sent emails (default: 24)")
    parser.add_argument("--interactive", action="store_true",
                        help="Interactive mode — chat with the agent")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("runner")

    # Create agent
    logger.info(f"Loading config: {args.config}")
    agent, config = create_agent(args.config)
    logger.info(f"Agent ready: {agent.name} (model: {config.model})")

    if args.interactive:
        # Interactive mode
        print(f"\n  Email Follow-Up Agent — Interactive Mode")
        print(f"  Client: {config.client_name}")
        print(f"  Email: {config.email.user_email}")
        print(f"  Type 'quit' to exit.\n")

        while True:
            try:
                user_input = input("You: ").strip()
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                if not user_input:
                    continue

                result = agent.run(user_input)
                print(f"\nAgent: {result.text}")
                print(f"  [{result.iterations} iterations, {result.total_input_tokens + result.total_output_tokens} tokens]\n")

            except KeyboardInterrupt:
                print("\nExiting.")
                break
    else:
        # Phase-based execution
        since = datetime.now() - timedelta(hours=args.since_hours)
        since_iso = since.isoformat()

        schedule_json = [
            {"number": s.number, "day_offset": s.day_offset, "template": s.template}
            for s in config.follow_up_schedule
        ]
        import json
        schedule_json_str = json.dumps(schedule_json)

        prompt_template = PHASE_PROMPTS[args.phase]
        prompt = prompt_template.format(since_iso=since_iso, schedule_json=schedule_json_str)

        logger.info(f"Running phase: {args.phase}")
        print(f"\n{'=' * 60}")
        print(f"  EMAIL FOLLOW-UP AGENT — Phase: {args.phase.upper()}")
        print(f"  Client: {config.client_name}")
        print(f"  Email: {config.email.user_email}")
        print(f"  Scanning since: {since.strftime('%Y-%m-%d %H:%M')}")
        print(f"{'=' * 60}\n")

        result = agent.run(prompt)

        print(f"\n{'─' * 60}")
        print(f"AGENT REPORT:")
        print(f"{'─' * 60}")
        print(result.text)
        print(f"{'─' * 60}")
        print(f"  Iterations: {result.iterations}")
        print(f"  Tokens: {result.total_input_tokens} in / {result.total_output_tokens} out")
        print(f"  Tool calls: {len(result.tool_calls_made)}")
        for tc in result.tool_calls_made:
            status = "ERROR" if tc["is_error"] else "OK"
            print(f"    • {tc['tool']} [{status}]")
        print()


if __name__ == "__main__":
    main()
