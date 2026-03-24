"""Tracing — structured JSON logging for every agent step.

Every tool call, LLM response, and decision gets logged to a JSONL file.
This is the foundation for evals (which read these traces) and for
debugging agent behavior.

Traces are saved to code/traces/<agent_name>_<timestamp>.jsonl

Each line is a JSON object:
    {"ts": "2026-03-24T09:01:23", "event": "tool_call_start", "data": {...}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Tracer protocol — anything that implements these methods works
# ---------------------------------------------------------------------------

class Tracer(Protocol):
    def start_run(self, agent_name: str, task: str, model: str) -> None: ...
    def log_event(self, event: str, data: dict[str, Any] | None = None) -> None: ...
    def end_run(self, result: Any) -> str | None: ...


# ---------------------------------------------------------------------------
# NullTracer — does nothing (default when you don't care about tracing)
# ---------------------------------------------------------------------------

class NullTracer:
    def start_run(self, agent_name: str, task: str, model: str) -> None:
        pass

    def log_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        pass

    def end_run(self, result: Any) -> str | None:
        return None


# ---------------------------------------------------------------------------
# JSONTracer — writes to a .jsonl file
# ---------------------------------------------------------------------------

_TRACES_DIR = Path(__file__).parent.parent / "traces"


class JSONTracer:
    """Writes structured trace events to a JSONL file.

    Usage:
        tracer = JSONTracer()
        agent = Agent(name="ar", tracer=tracer, ...)
        result = agent.run(...)
        print(f"Trace saved to: {result.trace_file}")
    """

    def __init__(self, output_dir: str | Path | None = None):
        self._output_dir = Path(output_dir) if output_dir else _TRACES_DIR
        self._file = None
        self._filepath: str | None = None
        self._run_start: datetime | None = None

    def start_run(self, agent_name: str, task: str, model: str) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._run_start = datetime.now(timezone.utc)

        timestamp = self._run_start.strftime("%Y%m%d_%H%M%S")
        filename = f"{agent_name}_{timestamp}.jsonl"
        self._filepath = str(self._output_dir / filename)
        self._file = open(self._filepath, "a", encoding="utf-8")

        self._write({
            "event": "run_start",
            "data": {
                "agent": agent_name,
                "task": task[:500],
                "model": model,
            },
        })

    def log_event(self, event: str, data: dict[str, Any] | None = None) -> None:
        if self._file is None:
            return
        self._write({"event": event, "data": data or {}})

    def end_run(self, result: Any) -> str | None:
        if self._file is None:
            return None

        duration = None
        if self._run_start:
            duration = (datetime.now(timezone.utc) - self._run_start).total_seconds()

        self._write({
            "event": "run_end",
            "data": {
                "iterations": getattr(result, "iterations", 0),
                "cost_usd": getattr(result, "cost_usd", 0.0),
                "tool_calls_count": len(getattr(result, "tool_calls", [])),
                "output_length": len(getattr(result, "output", "")),
                "duration_seconds": duration,
            },
        })

        self._file.close()
        self._file = None
        return self._filepath

    def _write(self, entry: dict) -> None:
        entry["ts"] = datetime.now(timezone.utc).isoformat()
        self._file.write(json.dumps(entry, default=str) + "\n")
        self._file.flush()


# ---------------------------------------------------------------------------
# Helper: read a trace file back
# ---------------------------------------------------------------------------

def read_trace(filepath: str | Path) -> list[dict]:
    """Read a JSONL trace file and return list of events."""
    events = []
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
