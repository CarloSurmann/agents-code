"""Fault Injector Hook — Injects faults into agent tool calls.

A standard pre_tool_use Hook that sits FIRST in the chain. It intercepts
tool calls and can raise exceptions, return error strings, or delay
execution based on FaultProfile rules.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from agency.agent import Hook, ToolCall
from proving_ground.faults.profiles import FaultProfile, FaultRule

logger = logging.getLogger(__name__)


class FaultInjectorHook(Hook):
    """Injects faults based on a FaultProfile.

    Goes FIRST in the hook chain so faults are applied before HITL/confidence.

    Usage:
        profile = load_fault_profile("fault_profiles/rate_limits.yaml")
        injector = FaultInjectorHook(profile)
        agent = Agent(hooks=[injector, confidence_gate, hitl, feedback, tracing])
    """

    def __init__(self, profile: FaultProfile, seed: int | None = None):
        self._profile = profile
        self._rng = random.Random(seed)
        self._call_counts: dict[str, int] = {}  # tool -> successful call count
        self._fault_counts: dict[str, int] = {}  # tool -> fault count

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        rules = self._profile.rules_for_tool(tool_call.name)
        if not rules:
            return True

        for rule in rules:
            if self._should_inject(tool_call.name, rule):
                self._inject_fault(tool_call, rule)
                self._fault_counts[tool_call.name] = self._fault_counts.get(tool_call.name, 0) + 1
                # For "block" faults, return False to prevent execution
                if rule.fault_type in ("rate_limit", "timeout"):
                    return True  # Let it through but the tool_call.metadata has the fault info
                # For others, we modify the tool call metadata so the tool result is an error
                return True

        self._call_counts[tool_call.name] = self._call_counts.get(tool_call.name, 0) + 1
        return True

    def _should_inject(self, tool_name: str, rule: FaultRule) -> bool:
        """Determine if a fault should be injected based on rule conditions."""
        # Check after_n_calls
        calls = self._call_counts.get(tool_name, 0)
        if calls < rule.after_n_calls:
            return False

        # Check max_faults
        faults = self._fault_counts.get(tool_name, 0)
        if rule.max_faults > 0 and faults >= rule.max_faults:
            return False

        # Check probability
        if self._rng.random() > rule.probability:
            return False

        return True

    def _inject_fault(self, tool_call: ToolCall, rule: FaultRule) -> None:
        """Apply the fault to the tool call."""
        logger.warning(f"[FaultInjector] Injecting {rule.fault_type} fault for {tool_call.name}")

        tool_call.metadata["_fault_injected"] = True
        tool_call.metadata["_fault_type"] = rule.fault_type
        tool_call.metadata["_fault_params"] = rule.params

        if rule.fault_type == "slow":
            delay = rule.params.get("delay_seconds", 2)
            time.sleep(delay)

        elif rule.fault_type == "error":
            msg = rule.params.get("message", "Simulated error")
            tool_call.metadata["_fault_error"] = msg

        elif rule.fault_type == "rate_limit":
            msg = rule.params.get("message", "Rate limit exceeded. Retry after 60s.")
            tool_call.metadata["_fault_error"] = msg

        elif rule.fault_type == "timeout":
            msg = rule.params.get("message", "Request timed out")
            tool_call.metadata["_fault_error"] = msg

        elif rule.fault_type == "malformed":
            field_name = rule.params.get("corrupt_field", "")
            value = rule.params.get("corrupt_value", "")
            if field_name and field_name in tool_call.input:
                tool_call.input[field_name] = value

    def post_tool_use(self, tool_call: ToolCall, result: Any) -> None:
        pass

    def get_stats(self) -> dict:
        """Get fault injection statistics."""
        return {
            "call_counts": dict(self._call_counts),
            "fault_counts": dict(self._fault_counts),
            "profile": self._profile.name,
        }
