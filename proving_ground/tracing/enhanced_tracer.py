"""Enhanced JSON Tracer — Extends JSONTracer with full I/O, cost, and latency.

Adds event types beyond the standard six:
- tool_call_full: complete tool input and output (not truncated)
- hitl_decision: human action, confidence, original/edited text
- cost_breakdown: per-LLM-call token counts and costs
- latency: wall-clock time for each tool call and LLM call

Subclasses JSONTracer so it's a drop-in replacement.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agency.tracing import JSONTracer


class EnhancedJSONTracer(JSONTracer):
    """JSONTracer with additional event types for the proving ground.

    Usage:
        tracer = EnhancedJSONTracer()
        agent = Agent(name="ar", tracer=tracer, ...)
        # Works identically to JSONTracer, but records more detail
    """

    def __init__(self, output_dir: str | Path | None = None):
        traces_dir = output_dir or (Path(__file__).parent.parent.parent / "traces" / "proving_ground")
        super().__init__(output_dir=traces_dir)
        self._tool_start_times: dict[str, float] = {}
        self._llm_start_time: float | None = None

    # ----- LLM call timing -----

    def log_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Override to add timing to LLM and tool events."""
        data = data or {}

        if event == "llm_call":
            self._llm_start_time = time.monotonic()

        if event == "tool_call_start":
            tool_name = data.get("tool", "")
            tool_id = data.get("tool_use_id", tool_name)
            self._tool_start_times[tool_id] = time.monotonic()

        if event == "tool_call_end":
            tool_name = data.get("tool", "")
            tool_id = data.get("tool_use_id", tool_name)
            start = self._tool_start_times.pop(tool_id, None)
            if start:
                data["latency_ms"] = round((time.monotonic() - start) * 1000, 1)

        super().log_event(event, data)

    # ----- Additional event types -----

    def log_tool_call_full(self, tool_name: str, tool_input: dict, tool_output: str, tool_use_id: str = "") -> None:
        """Log a tool call with complete (non-truncated) input and output."""
        self.log_event("tool_call_full", {
            "tool": tool_name,
            "tool_use_id": tool_use_id,
            "input": tool_input,
            "output": tool_output,
        })

    def log_hitl_decision(
        self,
        tool_name: str,
        action: str,
        confidence: float | None = None,
        confidence_band: str | None = None,
        original_draft: str | None = None,
        edited_text: str | None = None,
        source: str = "",
    ) -> None:
        """Log a HITL decision with full context."""
        self.log_event("hitl_decision", {
            "tool": tool_name,
            "action": action,
            "confidence": confidence,
            "confidence_band": confidence_band,
            "original_draft": original_draft,
            "edited_text": edited_text,
            "source": source,
        })

    def log_cost_breakdown(
        self,
        iteration: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
    ) -> None:
        """Log per-LLM-call cost breakdown."""
        latency_ms = None
        if self._llm_start_time:
            latency_ms = round((time.monotonic() - self._llm_start_time) * 1000, 1)
            self._llm_start_time = None

        self.log_event("cost_breakdown", {
            "iteration": iteration,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cost_usd": round(cost_usd, 6),
            "model": model,
            "latency_ms": latency_ms,
        })

    def log_scenario_event(self, action: str, params: dict) -> None:
        """Log a scenario engine event (timeline injection, fault, clock advance)."""
        self.log_event("scenario_event", {
            "action": action,
            "params": params,
        })
