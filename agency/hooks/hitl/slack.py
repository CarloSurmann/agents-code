"""
Slack HITL — Slack-based human-in-the-loop approval channel.

Implements the HITLChannel interface using Slack Block Kit messages
with interactive buttons and edit modals.

The critical mechanism: when the cron agent drafts a follow-up and calls
send_follow_up_reply, the PreToolUse hook fires send_approval_request().
This posts a Block Kit message to Slack with buttons and BLOCKS the agent
thread (via threading.Event) until a user clicks a button. The Bolt app's
action handler resolves the event, unblocking the agent.
"""

import logging
import threading
import uuid
from dataclasses import dataclass, field

from slack_sdk import WebClient

from agency.hooks.hitl.interface import HITLChannel, ApprovalResult, ApprovalAction
from agency.slack.blocks import build_approval_blocks, build_approved_blocks, build_notification_blocks

logger = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    """A follow-up draft waiting for human approval."""
    event: threading.Event
    result: ApprovalResult | None = None
    action_id: str = ""
    subject: str = ""
    body: str = ""
    recipient_name: str = ""
    recipient_email: str = ""
    message_ts: str = ""        # Slack message timestamp (for updating after action)
    channel_id: str = ""


class SlackHITL(HITLChannel):
    """
    Slack-based HITL channel.

    Posts Block Kit messages with Approve/Edit/Skip/Stop buttons.
    Blocks until a user clicks a button (or timeout).
    """

    def __init__(
        self,
        client: WebClient,
        channel_id: str,
        timeout_seconds: int = 3600,
    ):
        self.client = client
        self.channel_id = channel_id
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def send_approval_request(
        self,
        recipient_name: str,
        recipient_email: str,
        subject: str,
        body: str,
        follow_up_number: int,
        days_elapsed: int,
        context: str,
        item_type: str = "general",
    ) -> ApprovalResult:
        """
        Post a follow-up draft to Slack with approval buttons and BLOCK
        until a user clicks a button.
        """
        action_id = f"approval-{uuid.uuid4().hex[:8]}"

        # Build Block Kit message
        blocks = build_approval_blocks(
            action_id=action_id,
            recipient_name=recipient_name,
            recipient_email=recipient_email,
            subject=subject,
            body=body,
            follow_up_number=follow_up_number,
            days_elapsed=days_elapsed,
            context=context,
            item_type=item_type,
        )

        # Create pending approval with blocking event
        pending = PendingApproval(
            event=threading.Event(),
            action_id=action_id,
            subject=subject,
            body=body,
            recipient_name=recipient_name,
            recipient_email=recipient_email,
            channel_id=self.channel_id,
        )

        with self._lock:
            self._pending[action_id] = pending

        # Post to Slack
        try:
            result = self.client.chat_postMessage(
                channel=self.channel_id,
                blocks=blocks,
                text=f"Follow-up #{follow_up_number} for {recipient_name} ready for approval",
            )
            pending.message_ts = result["ts"]
            logger.info(f"Posted approval request {action_id} to Slack channel {self.channel_id}")
        except Exception as e:
            logger.error(f"Failed to post to Slack: {e}")
            with self._lock:
                self._pending.pop(action_id, None)
            return ApprovalResult(action=ApprovalAction.SKIP, reason=f"Slack error: {e}")

        # BLOCK until button is clicked or timeout
        logger.info(f"Waiting for approval on {action_id} (timeout: {self.timeout_seconds}s)...")
        pending.event.wait(timeout=self.timeout_seconds)

        # Retrieve result
        with self._lock:
            completed = self._pending.pop(action_id, None)

        if completed and completed.result:
            logger.info(f"Approval {action_id}: {completed.result.action.value}")
            return completed.result

        # Timeout — no response
        logger.warning(f"Approval {action_id} timed out after {self.timeout_seconds}s")
        return ApprovalResult(action=ApprovalAction.SKIP, reason="Timed out waiting for approval")

    def send_notification(self, message: str, emoji: str = "ℹ️") -> None:
        """Send a simple notification to the Slack channel."""
        blocks = build_notification_blocks(message, emoji)
        try:
            self.client.chat_postMessage(
                channel=self.channel_id,
                blocks=blocks,
                text=message,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    def resolve_action(
        self,
        action_id: str,
        action: ApprovalAction,
        edited_body: str | None = None,
        edited_subject: str | None = None,
        reason: str | None = None,
        user_id: str = "",
    ):
        """
        Called by the Bolt action handler when a user clicks a button.
        Unblocks the waiting send_approval_request() call.
        """
        with self._lock:
            pending = self._pending.get(action_id)

        if not pending:
            logger.warning(f"No pending approval found for {action_id}")
            return

        # Store the result
        pending.result = ApprovalResult(
            action=action,
            edited_body=edited_body,
            edited_subject=edited_subject,
            reason=reason,
        )

        # Update the Slack message to remove buttons and show outcome
        action_label = {
            ApprovalAction.APPROVE: "Approved",
            ApprovalAction.EDIT: "Edited & Sent",
            ApprovalAction.SKIP: "Skipped",
            ApprovalAction.STOP: "Stopped",
        }.get(action, "Unknown")

        try:
            # Fetch original message blocks to update them
            original = self.client.conversations_history(
                channel=pending.channel_id,
                latest=pending.message_ts,
                inclusive=True,
                limit=1,
            )
            if original["messages"]:
                old_blocks = original["messages"][0].get("blocks", [])
                new_blocks = build_approved_blocks(old_blocks, action_label, user_id)
                self.client.chat_update(
                    channel=pending.channel_id,
                    ts=pending.message_ts,
                    blocks=new_blocks,
                    text=f"Follow-up {action_label}",
                )
        except Exception as e:
            logger.warning(f"Could not update Slack message: {e}")

        # Unblock the waiting thread
        pending.event.set()
        logger.info(f"Resolved approval {action_id}: {action_label} by {user_id}")

    def get_pending(self, action_id: str) -> PendingApproval | None:
        """Get a pending approval (for the Edit modal to read subject/body)."""
        with self._lock:
            return self._pending.get(action_id)


def create_slack_hitl_hook(hitl: SlackHITL):
    """
    Create a PreToolUse hook function for the Slack HITL.

    Same pattern as create_console_hitl_hook() — returns a closure
    compatible with Agent's hook system.
    """

    def hook(tool_name: str, tool_input: dict) -> dict | None:
        # Only gate email sending tools
        send_tools = {"send_follow_up_reply", "mock_send_follow_up_reply"}
        if tool_name not in send_tools:
            return None

        result = hitl.send_approval_request(
            recipient_name=tool_input.get("to", "").split("<")[0].strip() if "<" in tool_input.get("to", "") else tool_input.get("to", ""),
            recipient_email=tool_input.get("to", ""),
            subject=tool_input.get("subject", ""),
            body=tool_input.get("body", ""),
            follow_up_number=0,  # Context would come from the tracker in production
            days_elapsed=0,
            context="",
        )

        if result.action == ApprovalAction.APPROVE:
            return None  # Proceed with original input

        elif result.action == ApprovalAction.EDIT:
            override = dict(tool_input)
            if result.edited_body:
                override["body"] = result.edited_body
            if result.edited_subject:
                override["subject"] = result.edited_subject
            return {"override_input": override}

        elif result.action == ApprovalAction.SKIP:
            return {"skip": True}

        elif result.action == ApprovalAction.STOP:
            return {"cancel": True, "reason": result.reason or "User stopped all follow-ups"}

        return None

    return hook
