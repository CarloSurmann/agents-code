"""Eval runner — execute eval suites against agent traces.

An eval reads a trace file and checks whether the agent behaved correctly.
Results are structured so they can be tracked over time (did quality improve
or regress after a prompt change?).

Usage:
    from agency.evals import run_eval, EvalResult
    from agency.tracing import read_trace

    trace = read_trace("traces/ar-follow-up_20260324_090000.jsonl")

    result = run_eval(
        name="correct_tool_sequence",
        trace=trace,
        check=lambda events: (
            events[1]["event"] == "tool_call_start"
            and events[1]["data"]["tool"] == "get_overdue_invoices"
        ),
    )
    print(result)  # EvalResult(name="correct_tool_sequence", passed=True, ...)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agency.tracing import read_trace

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Result of a single eval check."""
    name: str
    passed: bool
    score: float = 1.0          # 0.0 to 1.0 for graded evals
    details: str = ""           # human-readable explanation
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __str__(self) -> str:
        icon = "✅" if self.passed else "❌"
        score_str = f" ({self.score:.0%})" if self.score < 1.0 else ""
        detail_str = f" — {self.details}" if self.details else ""
        return f"{icon} {self.name}{score_str}{detail_str}"


def run_eval(
    name: str,
    trace: list[dict],
    check: Callable[[list[dict]], bool | tuple[bool, str]],
) -> EvalResult:
    """Run a single eval against a trace.

    Args:
        name: Name of the eval (e.g. "correct_tool_sequence")
        trace: List of trace events from read_trace()
        check: Function that takes events and returns:
               - True/False for pass/fail
               - (True/False, "explanation") for pass/fail with details

    Returns:
        EvalResult with pass/fail status
    """
    try:
        result = check(trace)

        if isinstance(result, tuple):
            passed, details = result
            return EvalResult(name=name, passed=passed, details=details)
        else:
            return EvalResult(name=name, passed=bool(result))

    except Exception as e:
        return EvalResult(
            name=name,
            passed=False,
            score=0.0,
            details=f"Eval crashed: {e}",
        )


def run_eval_suite(
    suite_name: str,
    trace_file: str | Path,
    checks: dict[str, Callable],
) -> list[EvalResult]:
    """Run multiple evals against a single trace file.

    Args:
        suite_name: Name of the eval suite
        trace_file: Path to a .jsonl trace file
        checks: Dict of {eval_name: check_function}

    Returns:
        List of EvalResults
    """
    trace = read_trace(trace_file)
    results = []

    logger.info(f"Running eval suite: {suite_name} ({len(checks)} checks)")

    for eval_name, check_fn in checks.items():
        result = run_eval(eval_name, trace, check_fn)
        results.append(result)
        logger.info(f"  {result}")

    passed = sum(1 for r in results if r.passed)
    logger.info(f"Suite {suite_name}: {passed}/{len(results)} passed")

    return results


# ---------------------------------------------------------------------------
# Built-in eval checks (reusable across agents)
# ---------------------------------------------------------------------------

def check_tool_was_called(tool_name: str) -> Callable:
    """Check that a specific tool was called at least once."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        calls = [
            e for e in events
            if e.get("event") == "tool_call_start"
            and e.get("data", {}).get("tool") == tool_name
        ]
        if calls:
            return True, f"Called {len(calls)} time(s)"
        return False, f"Tool '{tool_name}' was never called"
    return _check


def check_no_errors() -> Callable:
    """Check that no tool calls resulted in errors."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        errors = []
        for e in events:
            if e.get("event") == "tool_call_end":
                result = e.get("data", {}).get("result_preview", "")
                if result.startswith("Error:"):
                    errors.append(result)
        if not errors:
            return True, "No errors"
        return False, f"{len(errors)} error(s): {errors[0][:100]}"
    return _check


def check_completed_within(max_iterations: int) -> Callable:
    """Check that the agent finished within N iterations."""
    def _check(events: list[dict]) -> tuple[bool, str]:
        end = [e for e in events if e.get("event") == "run_end"]
        if not end:
            return False, "No run_end event found"
        iters = end[0].get("data", {}).get("iterations", 0)
        if iters <= max_iterations:
            return True, f"Completed in {iters} iterations"
        return False, f"Took {iters} iterations (max: {max_iterations})"
    return _check
