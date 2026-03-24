"""
HITL Channel Interface — Abstract base class for human-in-the-loop channels.

Every HITL channel (Slack, Teams, Telegram, Console) implements this interface.
The agent's PreToolUse hook calls these methods to get human approval.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class ApprovalAction(Enum):
    APPROVE = "approve"
    EDIT = "edit"
    SKIP = "skip"
    STOP = "stop"


@dataclass
class ApprovalResult:
    action: ApprovalAction
    edited_body: str | None = None     # Only set if action == EDIT
    edited_subject: str | None = None  # Only set if action == EDIT
    reason: str | None = None          # Optional reason for skip/stop


class HITLChannel(ABC):
    """Abstract base class for HITL notification channels."""

    @abstractmethod
    def send_approval_request(
        self,
        recipient_name: str,
        recipient_email: str,
        subject: str,
        body: str,
        follow_up_number: int,
        days_elapsed: int,
        context: str,
    ) -> ApprovalResult:
        """
        Send a follow-up draft for human approval.

        Blocks until the human responds.

        Returns:
            ApprovalResult with the human's decision.
        """
        ...

    @abstractmethod
    def send_notification(self, message: str) -> None:
        """
        Send an informational notification (no approval needed).

        Used for: "Jan replied!", weekly summaries, errors.
        """
        ...
