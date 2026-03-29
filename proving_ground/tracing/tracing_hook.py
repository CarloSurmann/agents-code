"""Enhanced Tracing Hook — Post-tool-use hook that captures full output.

This hook goes LAST in the hook chain (after FeedbackCapture) and records:
- Full tool output (not truncated to 200 chars like the default tracer)
- HITL decision metadata (from tool_call.metadata set by earlier hooks)
- Timing information

Uses EnhancedJSONTracer's additional event types.
"""

from __future__ import annotations

from typing import Any

from agency.agent import Hook, ToolCall
from proving_ground.tracing.enhanced_tracer import EnhancedJSONTracer


class EnhancedTracingHook(Hook):
    """Records full tool I/O and HITL decisions to the enhanced tracer.

    Usage:
        tracer = EnhancedJSONTracer()
        hook = EnhancedTracingHook(tracer)
        agent = Agent(hooks=[..., hook], tracer=tracer)  # hook goes LAST
    """

    def __init__(self, tracer: EnhancedJSONTracer):
        self._tracer = tracer

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        return True

    def post_tool_use(self, tool_call: ToolCall, result: Any) -> None:
        # Log full tool I/O
        output_str = result if isinstance(result, str) else str(result)
        self._tracer.log_tool_call_full(
            tool_name=tool_call.name,
            tool_input=tool_call.input,
            tool_output=output_str,
            tool_use_id=tool_call.tool_use_id,
        )

        # Log HITL decision if present in metadata
        meta = tool_call.metadata
        if "human_action" in meta or "confidence" in meta:
            self._tracer.log_hitl_decision(
                tool_name=tool_call.name,
                action=meta.get("human_action", "unknown"),
                confidence=meta.get("confidence"),
                confidence_band=meta.get("confidence_band"),
                original_draft=meta.get("original_draft"),
                edited_text=meta.get("edited_text"),
                source=meta.get("route_taken", ""),
            )
