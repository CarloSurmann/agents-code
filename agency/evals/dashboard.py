"""Eval dashboard — quick view of agent performance from trace files.

Run:
    python -m agency.evals.dashboard
    python -m agency.evals.dashboard --last 5       # last 5 runs
    python -m agency.evals.dashboard --verbose       # show full details

Shows:
- Run summary: duration, iterations, tool calls, cost
- Tool usage: which tools called, how often, success rate
- Latency breakdown: LLM time vs tool time
- Issues: errors, timeouts, missing data
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def load_traces(traces_dir: str = "traces", last_n: int = 0) -> list[list[dict]]:
    """Load trace files, return list of runs (each run is a list of events)."""
    traces_path = Path(traces_dir)
    if not traces_path.exists():
        return []

    files = sorted(traces_path.glob("*.jsonl"))
    if last_n > 0:
        files = files[-last_n:]

    runs = []
    for f in files:
        events = []
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        if events:
            runs.append(events)
    return runs


def analyze_run(events: list[dict]) -> dict:
    """Extract metrics from a single run's events."""
    metrics = {
        "task": "",
        "model": "",
        "agent": "",
        "start_time": "",
        "duration_seconds": 0,
        "iterations": 0,
        "cost_usd": 0,
        "tool_calls": [],
        "errors": [],
        "output_length": 0,
        "llm_calls": 0,
    }

    llm_start_times = {}
    tool_start_times = {}

    for event in events:
        ev = event.get("event", "")
        data = event.get("data", {})
        ts = event.get("ts", "")

        if ev == "run_start":
            metrics["task"] = data.get("task", "")[:80]
            metrics["model"] = data.get("model", "")
            metrics["agent"] = data.get("agent", "")
            metrics["start_time"] = ts

        elif ev == "run_end":
            metrics["duration_seconds"] = data.get("duration_seconds", 0)
            metrics["iterations"] = data.get("iterations", 0)
            metrics["cost_usd"] = data.get("cost_usd", 0)
            metrics["output_length"] = data.get("output_length", 0)

        elif ev == "llm_call":
            metrics["llm_calls"] += 1
            llm_start_times[data.get("iteration", 0)] = ts

        elif ev == "tool_call_start":
            tool_start_times[data.get("tool", "")] = ts

        elif ev == "tool_call_end":
            tool_name = data.get("tool", "")
            duration = 0
            if tool_name in tool_start_times:
                try:
                    start = datetime.fromisoformat(tool_start_times[tool_name])
                    end = datetime.fromisoformat(ts)
                    duration = (end - start).total_seconds()
                except (ValueError, TypeError):
                    pass

            metrics["tool_calls"].append({
                "tool": tool_name,
                "duration": round(duration, 2),
                "error": data.get("error"),
                "result_preview": (data.get("result_preview", ""))[:100],
            })

        elif ev == "error":
            metrics["errors"].append(data.get("message", str(data)))

    return metrics


def format_time(ts: str) -> str:
    """Format ISO timestamp to readable time."""
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts[:19]


def print_dashboard(traces_dir: str = "traces", last_n: int = 0, verbose: bool = False):
    """Print the eval dashboard."""
    runs = load_traces(traces_dir, last_n)

    if not runs:
        print("No traces found. Run the agent first!")
        return

    print()
    print("=" * 70)
    print("  AR FOLLOW-UP AGENT — EVAL DASHBOARD")
    print("=" * 70)
    print()

    total_runs = len(runs)
    total_tool_calls = 0
    total_errors = 0
    total_duration = 0
    total_cost = 0
    tool_usage: dict[str, int] = {}

    for i, events in enumerate(runs):
        m = analyze_run(events)
        total_tool_calls += len(m["tool_calls"])
        total_errors += len(m["errors"])
        total_duration += m["duration_seconds"]
        total_cost += m["cost_usd"]

        for tc in m["tool_calls"]:
            tool_usage[tc["tool"]] = tool_usage.get(tc["tool"], 0) + 1

    avg_duration = total_duration / total_runs if total_runs else 0

    # Summary
    print(f"  Runs analyzed:    {total_runs}")
    print(f"  Total duration:   {total_duration:.1f}s (avg {avg_duration:.1f}s per run)")
    print(f"  Total cost:       ${total_cost:.4f}")
    print(f"  Total tool calls: {total_tool_calls}")
    print(f"  Total errors:     {total_errors}")
    print()

    # Tool usage
    if tool_usage:
        print("  TOOL USAGE")
        print("  " + "-" * 45)
        for tool, count in sorted(tool_usage.items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 20)
            print(f"  {tool:30s} {count:3d} {bar}")
        print()

    # Per-run details
    print("  RUN HISTORY")
    print("  " + "-" * 66)
    print(f"  {'Time':<10} {'Task':<35} {'Duration':>8} {'Tools':>6} {'Err':>4}")
    print("  " + "-" * 66)

    for events in runs:
        m = analyze_run(events)
        time_str = format_time(m["start_time"])
        task_short = m["task"][:33] + ".." if len(m["task"]) > 35 else m["task"]
        err_str = str(len(m["errors"])) if m["errors"] else "—"
        print(f"  {time_str:<10} {task_short:<35} {m['duration_seconds']:>7.1f}s {len(m['tool_calls']):>5} {err_str:>4}")

    print()

    # Verbose: show each run in detail
    if verbose:
        print("  DETAILED RUNS")
        print("  " + "=" * 66)
        for i, events in enumerate(runs):
            m = analyze_run(events)
            print(f"\n  Run {i+1}: {m['task']}")
            print(f"  Model: {m['model']} | {m['iterations']} iterations | {m['duration_seconds']:.1f}s")
            print(f"  Output length: {m['output_length']} chars")

            if m["tool_calls"]:
                print("  Tools called:")
                for tc in m["tool_calls"]:
                    status = "✅" if not tc["error"] else "❌"
                    dur = f" ({tc['duration']}s)" if tc["duration"] > 0 else ""
                    print(f"    {status} {tc['tool']}{dur}")
                    if tc["result_preview"] and verbose:
                        print(f"       → {tc['result_preview']}")

            if m["errors"]:
                print("  Errors:")
                for err in m["errors"]:
                    print(f"    ❌ {err}")

            print("  " + "-" * 50)

    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent eval dashboard")
    parser.add_argument("--last", type=int, default=0, help="Show last N runs")
    parser.add_argument("--verbose", "-v", action="store_true", help="Detailed output")
    parser.add_argument("--traces", default="traces", help="Traces directory")
    args = parser.parse_args()

    print_dashboard(traces_dir=args.traces, last_n=args.last, verbose=args.verbose)


if __name__ == "__main__":
    main()
