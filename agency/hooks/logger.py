"""
Logger Hook — PostToolUse logging to file and SQLite.

Logs every tool call the agent makes: what tool, what input, what output, how long.
This is the "tracing" concept from Giovanni's architecture.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class ToolLogger:
    """PostToolUse hook that logs all tool executions."""

    def __init__(self, log_file: str = "agent_activity.log"):
        self.log_file = log_file

    def __call__(self, tool_name: str, tool_input: dict, result: str, duration_ms: float):
        """Log a tool execution."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "input_preview": json.dumps(tool_input)[:200],
            "output_preview": str(result)[:200],
            "duration_ms": round(duration_ms, 1),
        }

        logger.info(f"Tool: {tool_name} ({duration_ms:.0f}ms)")

        # Append to log file
        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Could not write to log file: {e}")
