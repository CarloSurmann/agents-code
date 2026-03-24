"""
Modals — Slack modal views for editing follow-up drafts.

When a user clicks "Edit" on an approval message, we open a modal
pre-filled with the current subject and body. They edit and submit,
which resolves the pending approval with the modified content.
"""

import logging

logger = logging.getLogger(__name__)


def open_edit_modal(client, trigger_id: str, action_id: str, subject: str, body: str):
    """
    Open a Slack modal pre-filled with the follow-up draft for editing.

    Args:
        client: Slack WebClient
        trigger_id: From the button click event (required by Slack to open modals)
        action_id: Our approval ID (passed through private_metadata)
        subject: Current email subject
        body: Current email body
    """
    try:
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "edit_followup_modal",
                "private_metadata": action_id,
                "title": {"type": "plain_text", "text": "Edit Follow-Up"},
                "submit": {"type": "plain_text", "text": "Send Edited"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "input",
                        "block_id": "subject_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "subject_input",
                            "initial_value": subject,
                            "placeholder": {"type": "plain_text", "text": "Email subject"},
                        },
                        "label": {"type": "plain_text", "text": "Subject"},
                    },
                    {
                        "type": "input",
                        "block_id": "body_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "body_input",
                            "multiline": True,
                            "initial_value": body,
                            "placeholder": {"type": "plain_text", "text": "Email body"},
                        },
                        "label": {"type": "plain_text", "text": "Email Body"},
                    },
                ],
            },
        )
        logger.info(f"Opened edit modal for approval {action_id}")
    except Exception as e:
        logger.error(f"Failed to open edit modal: {e}")
