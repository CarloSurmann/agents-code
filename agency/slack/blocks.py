"""
Block Kit — Slack message builders for the follow-up agent.

Builds rich Slack messages using Block Kit JSON format.
Inspired by OpenClaw's outbound adapter pattern.
"""


def build_approval_blocks(
    action_id: str,
    recipient_name: str,
    recipient_email: str,
    subject: str,
    body: str,
    follow_up_number: int,
    days_elapsed: int,
    context: str,
    item_type: str = "general",
) -> list[dict]:
    """Build Block Kit blocks for a follow-up approval request."""

    emoji = {"proposal": "📄", "quote": "💰", "invoice": "🧾",
             "request": "📋", "question": "❓"}.get(item_type, "📧")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} Follow-Up #{follow_up_number} Ready (Day {days_elapsed})",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*To:* {recipient_name}"},
                {"type": "mrkdwn", "text": f"*Type:* {item_type.title()}"},
                {"type": "mrkdwn", "text": f"*Email:* {recipient_email}"},
                {"type": "mrkdwn", "text": f"*Days since sent:* {days_elapsed}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Subject:* {subject}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"```\n{body}\n```"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"💡 *Context:* {context[:200]}"},
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"approval_{action_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Send"},
                    "style": "primary",
                    "action_id": "approve_followup",
                    "value": action_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✏️ Edit"},
                    "action_id": "edit_followup",
                    "value": action_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "⏸ Skip"},
                    "action_id": "skip_followup",
                    "value": action_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🛑 Stop All"},
                    "style": "danger",
                    "action_id": "stop_followup",
                    "value": action_id,
                },
            ],
        },
    ]


def build_approved_blocks(original_blocks: list[dict], action: str, user_name: str) -> list[dict]:
    """Replace the buttons with an outcome message after user acts."""
    emoji = {"Approved": "✅", "Skipped": "⏸", "Stopped": "🛑", "Edited & Sent": "✏️"}.get(action, "✅")
    # Keep header and content, replace the actions block
    result = []
    for block in original_blocks:
        if block.get("type") == "actions":
            result.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{emoji} *{action}* by <@{user_name}>"},
            })
        else:
            result.append(block)
    return result


def build_notification_blocks(message: str, emoji: str = "ℹ️") -> list[dict]:
    """Build a simple notification message."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} {message}"},
        },
    ]


def build_response_alert_blocks(
    recipient_name: str,
    subject: str,
    response_time_days: int,
) -> list[dict]:
    """Build a 'someone replied!' notification."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"✅ *{recipient_name}* replied to your email!\n"
                    f"*Subject:* {subject}\n"
                    f"*Response time:* {response_time_days} days"
                ),
            },
        },
    ]


def build_weekly_summary_blocks(stats: dict) -> list[dict]:
    """Build the weekly summary message."""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "📊 Weekly Follow-Up Report"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Active tracked:* {stats.get('active_tracked_items', 0)}"},
                {"type": "mrkdwn", "text": f"*Follow-ups sent:* {stats.get('follow_ups_sent_this_week', 0)}"},
                {"type": "mrkdwn", "text": f"*Responses received:* {stats.get('responses_received_this_week', 0)}"},
                {"type": "mrkdwn", "text": f"*Completed:* {stats.get('items_completed_this_week', 0)}"},
            ],
        },
    ]
