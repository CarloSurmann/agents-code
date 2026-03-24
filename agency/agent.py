"""
Agent — The core agentic loop with hook system.

Wraps the Anthropic Python SDK to provide:
- Automatic Python function → tool schema conversion
- PreToolUse / PostToolUse hooks (for HITL gating, logging, etc.)
- Cost tracking across iterations
- Structured result handling

Design: Giovanni's architecture (2026-03-24 session).
The agent loop is: observe → think → act → observe result → repeat.
"""

import json
import time
import inspect
import logging
from typing import Any, Callable
from dataclasses import dataclass, field

import anthropic

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Result from executing a tool."""
    tool_use_id: str
    output: str
    is_error: bool = False


@dataclass
class AgentResult:
    """Final result from an agent run."""
    text: str
    tool_calls_made: list[dict] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    iterations: int = 0


def python_function_to_tool_schema(func: Callable) -> dict:
    """
    Auto-convert a Python function into an Anthropic tool schema.

    Uses type hints and docstring to build the schema.
    The function's docstring becomes the tool description.
    Type hints become JSON Schema types.
    """
    hints = func.__annotations__
    sig = inspect.signature(func)

    # Map Python types to JSON Schema types
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        python_type = hints.get(param_name, str)
        json_type = type_map.get(python_type, "string")

        properties[param_name] = {
            "type": json_type,
            "description": f"Parameter: {param_name}",
        }

        # If no default value, it's required
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "name": func.__name__,
        "description": (func.__doc__ or f"Tool: {func.__name__}").strip(),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


class Agent:
    """
    The core agent class. Runs the agentic loop with hooks.

    Usage:
        agent = Agent(
            name="email-follow-up",
            model="claude-sonnet-4-20250514",
            system_prompt="You are an email follow-up agent...",
            tools=[watch_sent_folder, check_thread_for_reply, ...],
            hooks={"pre_tool_use": {"send_reply": hitl_hook}, "post_tool_use": {"*": logger_hook}},
        )
        result = agent.run("Check for new sent emails and process follow-ups")
    """

    def __init__(
        self,
        name: str,
        model: str = "claude-sonnet-4-20250514",
        system_prompt: str = "",
        tools: list[Callable] | None = None,
        hooks: dict | None = None,
        max_iterations: int = 20,
        api_key: str | None = None,
        tracer=None,
    ):
        self.name = name
        self.model = model
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.tracer = tracer  # Optional Tracer instance for observability

        # Anthropic client
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

        # Tools: store both the schemas (for API) and the callables (for execution)
        self._tool_functions: dict[str, Callable] = {}
        self._tool_schemas: list[dict] = []

        if tools:
            for func in tools:
                schema = python_function_to_tool_schema(func)
                self._tool_schemas.append(schema)
                self._tool_functions[func.__name__] = func

        # Hooks: {"pre_tool_use": {"tool_name": hook_fn}, "post_tool_use": {"*": hook_fn}}
        self._hooks = hooks or {}

    def _fire_pre_hook(self, tool_name: str, tool_input: dict) -> dict | None:
        """
        Fire PreToolUse hook. Returns None to proceed, or a dict to override/skip.

        The hook can return:
        - None → proceed with tool execution
        - {"skip": True} → skip this tool call
        - {"override_input": {...}} → use modified input
        - {"cancel": True, "reason": "..."} → cancel and return reason
        """
        pre_hooks = self._hooks.get("pre_tool_use", {})

        # Check for specific hook on this tool
        hook = pre_hooks.get(tool_name)
        if not hook:
            # Check for wildcard hook
            hook = pre_hooks.get("*")

        if hook:
            try:
                return hook(tool_name, tool_input)
            except Exception as e:
                logger.error(f"PreToolUse hook error for {tool_name}: {e}")

        return None

    def _fire_post_hook(self, tool_name: str, tool_input: dict, result: str, duration_ms: float):
        """Fire PostToolUse hook for logging/tracing."""
        post_hooks = self._hooks.get("post_tool_use", {})

        hook = post_hooks.get(tool_name) or post_hooks.get("*")

        if hook:
            try:
                hook(tool_name, tool_input, result, duration_ms)
            except Exception as e:
                logger.error(f"PostToolUse hook error for {tool_name}: {e}")

    def _execute_tool(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute a tool with pre/post hooks."""

        # --- PreToolUse Hook ---
        pre_result = self._fire_pre_hook(tool_name, tool_input)

        if pre_result:
            if pre_result.get("skip"):
                return ToolResult(
                    tool_use_id="",  # Will be set by caller
                    output="[Skipped by user]",
                )
            if pre_result.get("cancel"):
                return ToolResult(
                    tool_use_id="",
                    output=f"[Cancelled: {pre_result.get('reason', 'No reason given')}]",
                )
            if "override_input" in pre_result:
                tool_input = pre_result["override_input"]

        # --- Execute ---
        func = self._tool_functions.get(tool_name)
        if not func:
            return ToolResult(
                tool_use_id="",
                output=f"Error: Unknown tool '{tool_name}'",
                is_error=True,
            )

        start = time.time()
        try:
            result = func(**tool_input)
            output = json.dumps(result) if not isinstance(result, str) else result
            is_error = False
        except Exception as e:
            output = f"Error executing {tool_name}: {str(e)}"
            is_error = True
            logger.error(output)

        duration_ms = (time.time() - start) * 1000

        # --- PostToolUse Hook ---
        self._fire_post_hook(tool_name, tool_input, output, duration_ms)

        return ToolResult(tool_use_id="", output=output, is_error=is_error)

    def run(self, user_message: str) -> AgentResult:
        """
        Run the agent loop.

        Sends the user message to Claude with available tools.
        If Claude calls tools, executes them and loops back.
        Continues until Claude returns a final text response or max iterations hit.
        """
        messages = [{"role": "user", "content": user_message}]

        total_input_tokens = 0
        total_output_tokens = 0
        tool_calls_made = []

        for iteration in range(self.max_iterations):
            logger.info(f"[{self.name}] Iteration {iteration + 1}/{self.max_iterations}")

            # Build API request
            kwargs = {
                "model": self.model,
                "max_tokens": 4096,
                "messages": messages,
            }

            if self.system_prompt:
                kwargs["system"] = self.system_prompt

            if self._tool_schemas:
                kwargs["tools"] = self._tool_schemas

            # Call Claude
            call_start = time.time()
            try:
                response = self.client.messages.create(**kwargs)
            except anthropic.APIError as e:
                logger.error(f"[{self.name}] API error: {e}")
                return AgentResult(
                    text=f"API error: {str(e)}",
                    tool_calls_made=tool_calls_made,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    iterations=iteration + 1,
                )
            call_latency = (time.time() - call_start) * 1000

            # Track usage
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Record in tracer
            if self.tracer:
                tool_names_this_call = [
                    b.name for b in response.content if b.type == "tool_use"
                ]
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
                self.tracer.record_api_call(
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read_tokens=cache_read,
                    cache_write_tokens=cache_write,
                    latency_ms=call_latency,
                    stop_reason=response.stop_reason,
                    tool_calls=tool_names_this_call,
                )

            # Check stop reason
            if response.stop_reason == "end_turn":
                # Claude is done — extract final text
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text

                return AgentResult(
                    text=final_text,
                    tool_calls_made=tool_calls_made,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    iterations=iteration + 1,
                )

            if response.stop_reason == "tool_use":
                # Claude wants to use tools — execute them
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"[{self.name}] Tool call: {block.name}({json.dumps(block.input)[:200]})")

                        result = self._execute_tool(block.name, block.input)
                        result.tool_use_id = block.id

                        tool_calls_made.append({
                            "tool": block.name,
                            "input": block.input,
                            "output": result.output[:500],
                            "is_error": result.is_error,
                        })

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result.output,
                            **({"is_error": True} if result.is_error else {}),
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                # Unexpected stop reason
                logger.warning(f"[{self.name}] Unexpected stop_reason: {response.stop_reason}")
                return AgentResult(
                    text=f"Unexpected stop: {response.stop_reason}",
                    tool_calls_made=tool_calls_made,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    iterations=iteration + 1,
                )

        # Max iterations reached
        logger.warning(f"[{self.name}] Max iterations ({self.max_iterations}) reached")
        return AgentResult(
            text="Max iterations reached — agent stopped.",
            tool_calls_made=tool_calls_made,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            iterations=self.max_iterations,
        )
