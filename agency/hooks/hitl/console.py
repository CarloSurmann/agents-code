"""
Console HITL — Terminal-based human-in-the-loop for local development/testing.

Prints the draft to stdout and asks for approval via keyboard input.
No external services needed — perfect for testing the agent locally.
"""

import logging
from agency.hooks.hitl.interface import HITLChannel, ApprovalResult, ApprovalAction

logger = logging.getLogger(__name__)


class ConsoleHITL(HITLChannel):
    """Interactive console-based HITL for local testing."""

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
        """Print draft and ask for approval in terminal."""

        print("\n" + "=" * 60)
        print(f"  FOLLOW-UP #{follow_up_number} READY FOR APPROVAL")
        print("=" * 60)
        print(f"  To:      {recipient_name} <{recipient_email}>")
        print(f"  Subject: {subject}")
        print(f"  Days since original: {days_elapsed}")
        print(f"  Context: {context[:100]}...")
        print("-" * 60)
        print(f"\n{body}\n")
        print("-" * 60)
        print("  [S]end  |  [E]dit  |  [K]ip  |  S[T]op all follow-ups")
        print("=" * 60)

        while True:
            choice = input("\nYour choice (s/e/k/t): ").strip().lower()

            if choice == "s":
                logger.info("HITL: Approved")
                return ApprovalResult(action=ApprovalAction.APPROVE)

            elif choice == "e":
                print("\nEnter new subject (or press Enter to keep current):")
                new_subject = input(f"  [{subject}]: ").strip()
                print("\nEnter new body (type 'END' on a new line when done):")
                lines = []
                while True:
                    line = input()
                    if line.strip() == "END":
                        break
                    lines.append(line)
                new_body = "\n".join(lines) if lines else body

                return ApprovalResult(
                    action=ApprovalAction.EDIT,
                    edited_subject=new_subject if new_subject else None,
                    edited_body=new_body if lines else None,
                )

            elif choice == "k":
                reason = input("Reason for skipping (optional): ").strip()
                return ApprovalResult(action=ApprovalAction.SKIP, reason=reason or None)

            elif choice == "t":
                reason = input("Reason for stopping all (optional): ").strip()
                return ApprovalResult(action=ApprovalAction.STOP, reason=reason or None)

            else:
                print("Invalid choice. Please enter s, e, k, or t.")

    def send_notification(self, message: str) -> None:
        """Print notification to terminal."""
        print(f"\n  NOTIFICATION: {message}\n")


def create_console_hitl_hook(hitl: ConsoleHITL):
    """
    Create a PreToolUse hook function for the console HITL.

    Returns a function compatible with Agent's hook system.
    Only fires on 'send_follow_up_reply' tool calls.
    """

    def hook(tool_name: str, tool_input: dict) -> dict | None:
        if tool_name != "send_follow_up_reply":
            return None  # Only gate email sending

        result = hitl.send_approval_request(
            recipient_name=tool_input.get("to", "").split("<")[0].strip(),
            recipient_email=tool_input.get("to", ""),
            subject=tool_input.get("subject", ""),
            body=tool_input.get("body", ""),
            follow_up_number=0,  # Would come from context in production
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
