"""
Slack Bolt App — Event handlers, button actions, and modal submissions.

This is the central nervous system of the Slack integration.
Inspired by OpenClaw's gateway pattern: receives events, routes them to
the right handler, and dispatches responses.

Handlers:
- message (DM) → runs Agent.run() in thread pool → responds
- app_mention (@bot) → same as DM but in a channel
- approve/skip/stop buttons → resolve pending approval
- edit button → open modal
- edit modal submit → resolve with edits
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from slack_bolt import App

from agency.hooks.hitl.interface import ApprovalAction
from agency.hooks.hitl.slack import SlackHITL
from agency.slack.conversations import ConversationStore
from agency.slack.modals import open_edit_modal

logger = logging.getLogger(__name__)


def create_slack_app(
    bolt_app: App,
    agent_factory: Callable,
    slack_hitl: SlackHITL,
    conversation_store: ConversationStore | None = None,
    max_workers: int = 4,
) -> App:
    """
    Register all event handlers on the Bolt app.

    Args:
        bolt_app: The slack_bolt App instance
        agent_factory: Callable that returns a configured Agent instance
        slack_hitl: The SlackHITL instance (shared with cron agent)
        conversation_store: Per-user conversation history
        max_workers: Thread pool size for concurrent agent runs
    """
    conversations = conversation_store or ConversationStore()
    executor = ThreadPoolExecutor(max_workers=max_workers)

    # Track which users have an agent running (prevent concurrent runs)
    _running_users: set[str] = set()

    # ─── DM handler ────────────────────────────────────────────────

    @bolt_app.event("message")
    def handle_dm(event, say, client):
        """Handle direct messages to the bot."""
        # Ignore bot's own messages
        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user", "")
        text = event.get("text", "").strip()
        channel = event.get("channel", "")

        if not text:
            return

        # Prevent concurrent runs for same user
        if user_id in _running_users:
            say(text="⏳ I'm still working on your previous request. Please wait.", channel=channel)
            return

        _running_users.add(user_id)
        # Show typing indicator
        say(text="🤔 Thinking...", channel=channel)

        def run_agent():
            try:
                # Build prompt with conversation context
                prompt = conversations.build_context_prompt(user_id, text)

                # Create a fresh agent instance
                agent = agent_factory()
                result = agent.run(prompt)

                # Post response
                response_text = result.text or "I completed the task but have no text response."

                # Slack has a 3000 char limit for text blocks — split if needed
                if len(response_text) > 3000:
                    chunks = [response_text[i:i+3000] for i in range(0, len(response_text), 3000)]
                    for chunk in chunks:
                        client.chat_postMessage(channel=channel, text=chunk)
                else:
                    client.chat_postMessage(channel=channel, text=response_text)

                # Update conversation history
                conversations.add(user_id, "user", text)
                conversations.add(user_id, "assistant", response_text[:500])

                logger.info(f"Handled DM from {user_id}: {text[:50]}... → {result.iterations} iterations")

            except Exception as e:
                logger.error(f"Agent error for user {user_id}: {e}")
                client.chat_postMessage(
                    channel=channel,
                    text=f"❌ Sorry, I encountered an error: {str(e)[:200]}",
                )
            finally:
                _running_users.discard(user_id)

        executor.submit(run_agent)

    # ─── @mention handler ──────────────────────────────────────────

    @bolt_app.event("app_mention")
    def handle_mention(event, say, client):
        """Handle @bot mentions in channels."""
        user_id = event.get("user", "")
        text = event.get("text", "").strip()
        channel = event.get("channel", "")

        # Strip the @mention from the text
        # Slack sends: "<@U1234> show me pending" — remove the <@...> part
        import re
        text = re.sub(r"<@\w+>\s*", "", text).strip()

        if not text:
            say(text="Hi! You can ask me things like:\n• _Show me what's pending_\n• _How many follow-ups were sent this week?_\n• _Stop tracking the proposal for Jan_", channel=channel)
            return

        if user_id in _running_users:
            say(text="⏳ Working on your previous request...", channel=channel)
            return

        _running_users.add(user_id)

        def run_agent():
            try:
                prompt = conversations.build_context_prompt(user_id, text)
                agent = agent_factory()
                result = agent.run(prompt)

                response_text = result.text or "Done."
                client.chat_postMessage(channel=channel, text=response_text)

                conversations.add(user_id, "user", text)
                conversations.add(user_id, "assistant", response_text[:500])
            except Exception as e:
                logger.error(f"Agent error (mention) for {user_id}: {e}")
                client.chat_postMessage(channel=channel, text=f"❌ Error: {str(e)[:200]}")
            finally:
                _running_users.discard(user_id)

        executor.submit(run_agent)

    # ─── Button: Approve ───────────────────────────────────────────

    @bolt_app.action("approve_followup")
    def handle_approve(ack, body):
        ack()
        action_id = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        logger.info(f"Approve clicked by {user_id} for {action_id}")
        slack_hitl.resolve_action(action_id, ApprovalAction.APPROVE, user_id=user_id)

    # ─── Button: Skip ─────────────────────────────────────────────

    @bolt_app.action("skip_followup")
    def handle_skip(ack, body):
        ack()
        action_id = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        logger.info(f"Skip clicked by {user_id} for {action_id}")
        slack_hitl.resolve_action(action_id, ApprovalAction.SKIP, reason="Skipped by user", user_id=user_id)

    # ─── Button: Stop All ─────────────────────────────────────────

    @bolt_app.action("stop_followup")
    def handle_stop(ack, body):
        ack()
        action_id = body["actions"][0]["value"]
        user_id = body["user"]["id"]
        logger.info(f"Stop clicked by {user_id} for {action_id}")
        slack_hitl.resolve_action(action_id, ApprovalAction.STOP, reason="All follow-ups stopped by user", user_id=user_id)

    # ─── Button: Edit (opens modal) ───────────────────────────────

    @bolt_app.action("edit_followup")
    def handle_edit(ack, body, client):
        ack()
        action_id = body["actions"][0]["value"]
        trigger_id = body["trigger_id"]

        # Get the current draft content from pending approvals
        pending = slack_hitl.get_pending(action_id)
        if pending:
            open_edit_modal(client, trigger_id, action_id, pending.subject, pending.body)
        else:
            logger.warning(f"Edit clicked but no pending approval found for {action_id}")

    # ─── Modal: Edit submission ────────────────────────────────────

    @bolt_app.view("edit_followup_modal")
    def handle_edit_submit(ack, body, view):
        ack()
        action_id = view["private_metadata"]
        user_id = body["user"]["id"]
        values = view["state"]["values"]

        new_subject = values["subject_block"]["subject_input"]["value"]
        new_body = values["body_block"]["body_input"]["value"]

        logger.info(f"Edit submitted by {user_id} for {action_id}")
        slack_hitl.resolve_action(
            action_id,
            ApprovalAction.EDIT,
            edited_subject=new_subject,
            edited_body=new_body,
            user_id=user_id,
        )

    return bolt_app
