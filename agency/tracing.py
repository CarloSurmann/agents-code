"""
Tracing — Full observability for every agent run.

Captures EVERYTHING:
- Every API call (model, tokens in/out, cost, latency)
- Every tool call (name, input, output, duration)
- Every decision the agent made (and why)
- Classification results (rules vs LLM)
- HITL decisions

Outputs a structured trace that can be:
- Printed to terminal (pretty format)
- Saved to JSON (for analysis)
- Viewed in the HTML dashboard

Design: Giovanni's tracing concept (PostToolUse logging → Langfuse).
This is the local-first version before Langfuse integration.
"""

import json
import time
import logging
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Pricing database (from docs.anthropic.com, March 2026) ────────

MODEL_PRICING = {
    # model_id: (input_$/MTok, output_$/MTok, cache_read_$/MTok, cache_write_5m_$/MTok)
    "claude-opus-4-6":    (5.00,  25.00, 0.50, 6.25),
    "claude-opus-4-5":    (5.00,  25.00, 0.50, 6.25),
    "claude-sonnet-4-6":  (3.00,  15.00, 0.30, 3.75),
    "claude-sonnet-4-5":  (3.00,  15.00, 0.30, 3.75),
    "claude-sonnet-4-0":  (3.00,  15.00, 0.30, 3.75),
    "claude-haiku-4-5":   (1.00,   5.00, 0.10, 1.25),
    # Legacy
    "claude-opus-4-0":    (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-1":    (15.00, 75.00, 1.50, 18.75),
}

EUR_USD = 0.92


def get_pricing(model: str) -> tuple[float, float, float, float]:
    """Get (input, output, cache_read, cache_write) pricing per MTok."""
    # Try exact match first, then prefix match
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    for key, val in MODEL_PRICING.items():
        if model.startswith(key.rsplit("-", 1)[0]):
            return val
    # Default to Sonnet pricing
    return (3.00, 15.00, 0.30, 3.75)


@dataclass
class APICallTrace:
    """One API call to Claude."""
    call_id: int
    timestamp: str
    phase: str              # "classify", "scan", "check", "followup"
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0
    stop_reason: str = ""
    tool_calls: list[str] = field(default_factory=list)
    cost_usd: float = 0


@dataclass
class ToolCallTrace:
    """One tool execution."""
    call_id: int
    api_call_id: int        # Which API call triggered this
    tool_name: str
    input_preview: str
    output_preview: str
    duration_ms: float
    phase: str


@dataclass
class ClassificationTrace:
    """One email classification."""
    email_subject: str
    email_to: str
    classified_by: str      # "rules" or "llm"
    should_track: bool
    reason: str
    item_type: str
    api_tokens_used: int    # 0 if rules, >0 if LLM


@dataclass
class RunTrace:
    """Complete trace for one agent run (one cron cycle or one command)."""
    run_id: str
    started_at: str
    ended_at: str = ""
    phase: str = "full"
    model: str = ""
    config_name: str = ""

    # Collected traces
    api_calls: list[APICallTrace] = field(default_factory=list)
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    classifications: list[ClassificationTrace] = field(default_factory=list)

    # Aggregates (computed at end)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_cost_usd: float = 0
    total_cost_eur: float = 0
    total_api_calls: int = 0
    total_tool_calls: int = 0
    total_latency_ms: float = 0
    emails_scanned: int = 0
    emails_tracked: int = 0
    emails_skipped_rules: int = 0
    emails_skipped_llm: int = 0
    responses_detected: int = 0
    follow_ups_sent: int = 0


class Tracer:
    """
    Collects traces during an agent run.

    Usage:
        tracer = Tracer(model="claude-opus-4-6", phase="full")

        # As PostToolUse hook:
        hooks = {"post_tool_use": {"*": tracer.tool_hook()}}

        # After run:
        trace = tracer.finish()
        tracer.print_summary()
        tracer.save("traces/run_001.json")
    """

    def __init__(self, model: str = "", phase: str = "full", config_name: str = ""):
        self._trace = RunTrace(
            run_id=f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            started_at=datetime.now().isoformat(),
            phase=phase,
            model=model,
            config_name=config_name,
        )
        self._api_call_counter = 0
        self._tool_call_counter = 0
        self._pricing = get_pricing(model)

    def record_api_call(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_ms: float = 0,
        stop_reason: str = "",
        tool_calls: list[str] | None = None,
        phase: str = "",
    ):
        """Record a Claude API call."""
        self._api_call_counter += 1
        inp_rate, out_rate, cr_rate, cw_rate = self._pricing

        cost = (
            (input_tokens / 1e6) * inp_rate
            + (output_tokens / 1e6) * out_rate
            + (cache_read_tokens / 1e6) * cr_rate
            + (cache_write_tokens / 1e6) * cw_rate
        )

        call = APICallTrace(
            call_id=self._api_call_counter,
            timestamp=datetime.now().isoformat(),
            phase=phase or self._trace.phase,
            model=self._trace.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            tool_calls=tool_calls or [],
            cost_usd=cost,
        )
        self._trace.api_calls.append(call)

    def record_classification(
        self,
        email_subject: str,
        email_to: str,
        classified_by: str,
        should_track: bool,
        reason: str,
        item_type: str = "",
        api_tokens_used: int = 0,
    ):
        """Record a classification decision."""
        cls = ClassificationTrace(
            email_subject=email_subject,
            email_to=email_to,
            classified_by=classified_by,
            should_track=should_track,
            reason=reason,
            item_type=item_type,
            api_tokens_used=api_tokens_used,
        )
        self._trace.classifications.append(cls)

        if not should_track:
            if classified_by == "rules":
                self._trace.emails_skipped_rules += 1
            else:
                self._trace.emails_skipped_llm += 1
        else:
            self._trace.emails_tracked += 1

    def tool_hook(self):
        """Return a PostToolUse hook function for the Agent."""
        def hook(tool_name: str, tool_input: dict, result: str, duration_ms: float):
            self._tool_call_counter += 1
            self._trace.tool_calls.append(ToolCallTrace(
                call_id=self._tool_call_counter,
                api_call_id=self._api_call_counter,
                tool_name=tool_name,
                input_preview=json.dumps(tool_input)[:200],
                output_preview=str(result)[:200],
                duration_ms=duration_ms,
                phase=self._trace.phase,
            ))
        return hook

    def finish(self) -> RunTrace:
        """Finalize the trace with aggregated metrics."""
        t = self._trace
        t.ended_at = datetime.now().isoformat()
        t.total_api_calls = len(t.api_calls)
        t.total_tool_calls = len(t.tool_calls)
        t.total_input_tokens = sum(c.input_tokens for c in t.api_calls)
        t.total_output_tokens = sum(c.output_tokens for c in t.api_calls)
        t.total_cache_read = sum(c.cache_read_tokens for c in t.api_calls)
        t.total_cache_write = sum(c.cache_write_tokens for c in t.api_calls)
        t.total_cost_usd = sum(c.cost_usd for c in t.api_calls)
        t.total_cost_eur = t.total_cost_usd * EUR_USD
        t.total_latency_ms = sum(c.latency_ms for c in t.api_calls)
        t.emails_scanned = t.emails_tracked + t.emails_skipped_rules + t.emails_skipped_llm
        return t

    def print_summary(self):
        """Print a pretty summary to terminal."""
        t = self.finish() if not self._trace.ended_at else self._trace

        print(f"\n{'═' * 70}")
        print(f"  TRACE: {t.run_id}")
        print(f"  Model: {t.model}  |  Phase: {t.phase}  |  Config: {t.config_name}")
        print(f"{'═' * 70}")

        # API calls breakdown
        print(f"\n  API CALLS ({t.total_api_calls} total):")
        print(f"  {'#':<4} {'Phase':<12} {'Input':>8} {'Output':>8} {'Cache R':>8} {'Cost $':>8} {'Latency':>8} {'Tools'}")
        print(f"  {'─'*4} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*20}")
        for c in t.api_calls:
            tools_str = ", ".join(c.tool_calls[:3]) + ("..." if len(c.tool_calls) > 3 else "")
            print(f"  {c.call_id:<4} {c.phase:<12} {c.input_tokens:>8,} {c.output_tokens:>8,} {c.cache_read_tokens:>8,} ${c.cost_usd:>7.4f} {c.latency_ms:>7.0f}ms {tools_str}")

        # Classifications
        if t.classifications:
            print(f"\n  CLASSIFICATIONS ({len(t.classifications)} emails):")
            print(f"  {'Subject':<40} {'By':<6} {'Track':>5} {'Type':<10} {'Reason'}")
            print(f"  {'─'*40} {'─'*6} {'─'*5} {'─'*10} {'─'*30}")
            for c in t.classifications:
                track = "✅" if c.should_track else "❌"
                print(f"  {c.email_subject[:40]:<40} {c.classified_by:<6} {track:>5} {c.item_type:<10} {c.reason[:30]}")

        # Tool calls summary
        if t.tool_calls:
            tool_counts = {}
            for tc in t.tool_calls:
                tool_counts[tc.tool_name] = tool_counts.get(tc.tool_name, 0) + 1
            print(f"\n  TOOL CALLS ({t.total_tool_calls} total):")
            for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
                print(f"    {name}: {count}x")

        # Cost summary
        print(f"\n  {'─' * 70}")
        print(f"  TOTALS:")
        print(f"    Input tokens:   {t.total_input_tokens:>10,}")
        print(f"    Output tokens:  {t.total_output_tokens:>10,}")
        print(f"    Cache reads:    {t.total_cache_read:>10,}")
        print(f"    Total cost:     ${t.total_cost_usd:>9.4f}  (€{t.total_cost_eur:.4f})")
        print(f"    Total latency:  {t.total_latency_ms/1000:>9.1f}s")
        print(f"    Emails: {t.emails_scanned} scanned, {t.emails_tracked} tracked, "
              f"{t.emails_skipped_rules} rules-skip, {t.emails_skipped_llm} llm-skip")
        print(f"{'═' * 70}\n")

    def save(self, path: str):
        """Save trace to JSON file."""
        t = self.finish() if not self._trace.ended_at else self._trace
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        # Convert to serializable dict
        data = {
            "run_id": t.run_id, "started_at": t.started_at, "ended_at": t.ended_at,
            "phase": t.phase, "model": t.model, "config_name": t.config_name,
            "totals": {
                "api_calls": t.total_api_calls, "tool_calls": t.total_tool_calls,
                "input_tokens": t.total_input_tokens, "output_tokens": t.total_output_tokens,
                "cache_read_tokens": t.total_cache_read, "cache_write_tokens": t.total_cache_write,
                "cost_usd": round(t.total_cost_usd, 6), "cost_eur": round(t.total_cost_eur, 6),
                "latency_ms": round(t.total_latency_ms, 1),
                "emails_scanned": t.emails_scanned, "emails_tracked": t.emails_tracked,
                "responses_detected": t.responses_detected, "follow_ups_sent": t.follow_ups_sent,
            },
            "api_calls": [
                {"call_id": c.call_id, "phase": c.phase, "input_tokens": c.input_tokens,
                 "output_tokens": c.output_tokens, "cache_read": c.cache_read_tokens,
                 "cost_usd": round(c.cost_usd, 6), "latency_ms": round(c.latency_ms, 1),
                 "stop_reason": c.stop_reason, "tool_calls": c.tool_calls}
                for c in t.api_calls
            ],
            "classifications": [
                {"subject": c.email_subject, "to": c.email_to, "classified_by": c.classified_by,
                 "should_track": c.should_track, "reason": c.reason, "item_type": c.item_type}
                for c in t.classifications
            ],
            "tool_calls": [
                {"tool": tc.tool_name, "input": tc.input_preview, "output": tc.output_preview,
                 "duration_ms": round(tc.duration_ms, 1), "phase": tc.phase}
                for tc in t.tool_calls
            ],
        }

        p.write_text(json.dumps(data, indent=2))
        logger.info(f"Trace saved: {path}")
