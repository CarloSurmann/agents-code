"""Core Agent class — agentic loop with swappable LLM backend.

Supports two backends:
- "ollama/<model>" — local Ollama for development (free, fast, no API key)
- "claude-sonnet-4-6" etc. — Anthropic API for production

The Agent handles the agentic loop (observe -> decide -> act -> repeat)
using native tool_use protocol. You provide tools, skills, and hooks;
the Agent wires them together and runs the loop.

Tools:   Python functions the agent can call (actions in the world)
Skills:  Markdown files loaded into the system prompt (knowledge + instructions)
Hooks:   Your code that runs before/after tool calls (HITL, logging, etc.)
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from agency.skills import load_skills
from agency.tracing import Tracer, NullTracer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type mappings: Python types -> JSON Schema types
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


# ---------------------------------------------------------------------------
# Tool schema generation
# ---------------------------------------------------------------------------

def _function_to_tool_schema(func: Callable) -> dict:
    """Convert a Python function into a tool schema.

    Uses the function's name, docstring, type hints, and default values
    to build the schema automatically.
    """
    hints = {}
    try:
        hints = inspect.get_annotations(func, eval_str=True)
    except Exception:
        hints = getattr(func, "__annotations__", {})

    sig = inspect.signature(func)

    properties = {}
    required = []

    for name, param in sig.parameters.items():
        python_type = hints.get(name, str)
        json_type = _TYPE_MAP.get(python_type, "string")
        prop: dict[str, Any] = {"type": json_type}

        if param.default is not inspect.Parameter.empty:
            prop["description"] = f"Default: {param.default}"
        else:
            required.append(name)

        properties[name] = prop

    return {
        "name": func.__name__,
        "description": (inspect.getdoc(func) or "").strip(),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# ---------------------------------------------------------------------------
# Hook protocol
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """Represents a tool call the agent wants to make."""
    name: str
    input: dict[str, Any]
    tool_use_id: str


class Hook:
    """Base class for hooks. Subclass and override the methods you need."""

    def pre_tool_use(self, tool_call: ToolCall) -> bool:
        """Called before a tool executes. Return False to block it."""
        return True

    def post_tool_use(self, tool_call: ToolCall, result: Any) -> None:
        """Called after a tool executes. Use for logging, tracing, etc."""
        pass


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """What the agent returns after a run."""
    output: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    iterations: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    trace_file: str | None = None


# ---------------------------------------------------------------------------
# LLM Backends
# ---------------------------------------------------------------------------

class _AnthropicBackend:
    """Production backend — calls Claude via Anthropic API."""

    def __init__(self, model: str):
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic()

    def chat(self, system: str, messages: list, tools: list) -> dict:
        response = self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )
        return self._normalize(response)

    def _normalize(self, response) -> dict:
        """Convert Anthropic response to our common format."""
        content = []
        for block in response.content:
            if hasattr(block, "text"):
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        cost = 0.0
        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "usage"):
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            input_cost = (input_tokens / 1_000_000) * 3.0
            output_cost = (output_tokens / 1_000_000) * 15.0
            cost = input_cost + output_cost

        return {
            "content": content,
            "stop_reason": response.stop_reason,
            "cost": cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


class _OllamaBackend:
    """Dev backend — calls local Ollama. Free, no API key needed.

    Ollama doesn't natively support Anthropic's tool_use protocol, so we
    inject tool descriptions into the system prompt and parse JSON tool
    calls from the model's text output.
    """

    def __init__(self, model: str):
        import httpx
        self.model = model
        self.base_url = "http://localhost:11434"
        self._httpx = httpx

    def chat(self, system: str, messages: list, tools: list) -> dict:
        # Inject tool descriptions into system prompt
        tool_instructions = self._build_tool_prompt(tools)
        full_system = f"{system}\n\n{tool_instructions}"

        # Convert messages to Ollama format
        ollama_messages = [{"role": "system", "content": full_system}]
        for msg in messages:
            if msg["role"] == "assistant":
                # Extract text from content blocks
                text = self._extract_text(msg["content"])
                if text:
                    ollama_messages.append({"role": "assistant", "content": text})
            elif msg["role"] == "user":
                if isinstance(msg["content"], str):
                    ollama_messages.append({"role": "user", "content": msg["content"]})
                elif isinstance(msg["content"], list):
                    # Tool results — format them as text
                    parts = []
                    for item in msg["content"]:
                        if isinstance(item, dict) and item.get("type") == "tool_result":
                            parts.append(f"Tool result: {item.get('content', '')}")
                    if parts:
                        ollama_messages.append({"role": "user", "content": "\n".join(parts)})

        response = self._httpx.post(
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": ollama_messages, "stream": False},
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()

        return self._parse_response(data.get("message", {}).get("content", ""))

    def _build_tool_prompt(self, tools: list) -> str:
        if not tools:
            return ""

        lines = [
            "## Available Tools",
            "You can call tools by responding with a JSON block like this:",
            '```json\n{"tool": "tool_name", "input": {"param": "value"}}\n```',
            "",
            "IMPORTANT: When you want to call a tool, respond ONLY with the JSON block, nothing else.",
            "When you are done and have no more tools to call, respond with regular text.",
            "",
            "Tools:",
        ]
        for t in tools:
            params = t["input_schema"].get("properties", {})
            param_list = ", ".join(f"{k}: {v.get('type', 'string')}" for k, v in params.items())
            lines.append(f"- **{t['name']}**({param_list}): {t['description']}")

        return "\n".join(lines)

    def _extract_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        parts.append(json.dumps({"tool": block["name"], "input": block["input"]}))
            return "\n".join(parts)
        return str(content)

    def _parse_response(self, text: str) -> dict:
        """Try to parse tool calls from the model's text output."""
        # Try to find JSON tool call in the response
        tool_call = self._extract_json_tool_call(text)

        if tool_call:
            return {
                "content": [{
                    "type": "tool_use",
                    "id": f"ollama_{id(text)}",
                    "name": tool_call["tool"],
                    "input": tool_call.get("input", {}),
                }],
                "stop_reason": "tool_use",
                "cost": 0.0,
            }
        else:
            return {
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "cost": 0.0,
            }

    def _extract_json_tool_call(self, text: str) -> dict | None:
        """Extract a JSON tool call from model output."""
        # Try the whole text as JSON first
        cleaned = text.strip()

        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            inner_lines = []
            started = False
            for line in lines:
                if not started and line.strip().startswith("```"):
                    started = True
                    continue
                if started and line.strip() == "```":
                    break
                if started:
                    inner_lines.append(line)
            cleaned = "\n".join(inner_lines).strip()

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "tool" in parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        # Try to find JSON embedded in text
        for start_char in ["{", "["]:
            idx = text.find(start_char)
            if idx == -1:
                continue
            # Find matching closing bracket
            depth = 0
            for i in range(idx, len(text)):
                if text[i] in "{[":
                    depth += 1
                elif text[i] in "}]":
                    depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[idx:i + 1])
                        if isinstance(parsed, dict) and "tool" in parsed:
                            return parsed
                    except json.JSONDecodeError:
                        pass
                    break

        return None


def _create_backend(model: str):
    """Create the right backend based on model string."""
    if model.startswith("ollama/"):
        ollama_model = model[len("ollama/"):]
        return _OllamaBackend(ollama_model)
    else:
        return _AnthropicBackend(model)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """An agent that uses an LLM to decide which tools to call.

    Example:
        # Dev mode (free, local)
        agent = Agent(name="test", model="ollama/qwen3.5:9b", tools=[...])

        # Production (Claude API)
        agent = Agent(name="test", model="claude-sonnet-4-6", tools=[...])
    """

    def __init__(
        self,
        name: str,
        system_prompt: str = "",
        tools: list[Callable] | None = None,
        skills: list[str] | None = None,
        skills_dir: str | None = None,
        hooks: list[Hook] | None = None,
        model: str = "ollama/qwen3.5:9b",
        max_iterations: int = 20,
        tracer: Tracer | None = None,
    ):
        self.name = name
        self.model = model
        self.max_iterations = max_iterations

        # LLM backend (Ollama or Anthropic)
        self._backend = _create_backend(model)

        # Register tools
        self._tool_fns: dict[str, Callable] = {}
        self._tool_schemas: list[dict] = []
        for func in tools or []:
            schema = _function_to_tool_schema(func)
            self._tool_fns[schema["name"]] = func
            self._tool_schemas.append(schema)

        # Build system prompt: base prompt + date context + loaded skills
        from datetime import datetime
        date_context = (
            f"## Current Date and Time\n"
            f"Today is {datetime.now().strftime('%A, %B %d, %Y')} "
            f"({datetime.now().strftime('%Y-%m-%d')}). "
            f"Current time: {datetime.now().strftime('%H:%M')} local time.\n"
            f"Use this for any date calculations — never ask the user for today's date."
        )
        skill_content = load_skills(skills or [], skills_dir=skills_dir)
        parts = [p for p in [system_prompt, date_context, skill_content] if p]
        self._system_prompt = "\n\n".join(parts)

        # Hooks
        self._hooks = hooks or []

        # Tracing
        self._tracer = tracer or NullTracer()

    def run(self, task: str) -> AgentResult:
        """Run the agentic loop until the model stops or we hit max iterations."""
        messages: list[dict] = [{"role": "user", "content": task}]
        result = AgentResult()

        self._tracer.start_run(self.name, task, self.model)

        for i in range(self.max_iterations):
            result.iterations = i + 1
            logger.info(f"[{self.name}] iteration {i + 1}")

            # Call LLM
            self._tracer.log_event("llm_call", {"iteration": i + 1})
            response = self._backend.chat(
                system=self._system_prompt,
                messages=messages,
                tools=self._tool_schemas,
            )
            result.cost_usd += response.get("cost", 0.0)
            result.total_input_tokens += response.get("input_tokens", 0)
            result.total_output_tokens += response.get("output_tokens", 0)

            # Build assistant message for history
            messages.append({"role": "assistant", "content": response["content"]})

            # If the model is done (no tool calls), extract final text
            if response["stop_reason"] == "end_turn":
                for block in response["content"]:
                    if block.get("type") == "text":
                        result.output += block["text"]
                self._tracer.log_event("end_turn", {"output_length": len(result.output)})
                break

            # Process tool calls
            tool_results = []
            for block in response["content"]:
                if block.get("type") != "tool_use":
                    continue

                tool_call = ToolCall(
                    name=block["name"],
                    input=block["input"],
                    tool_use_id=block["id"],
                )

                self._tracer.log_event("tool_call_start", {
                    "tool": block["name"],
                    "input": block["input"],
                })

                # --- Pre-tool hooks ---
                approved = True
                for hook in self._hooks:
                    if not hook.pre_tool_use(tool_call):
                        approved = False
                        logger.info(f"[{self.name}] tool '{block['name']}' blocked by hook")
                        break

                if not approved:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "Action was rejected by human reviewer.",
                    })
                    result.tool_calls.append({
                        "tool": block["name"],
                        "input": block["input"],
                        "result": "REJECTED",
                    })
                    self._tracer.log_event("tool_rejected", {"tool": block["name"]})
                    continue

                # --- Execute the tool ---
                fn = self._tool_fns.get(block["name"])
                if fn is None:
                    tool_output = f"Error: unknown tool '{block['name']}'"
                else:
                    try:
                        tool_output = fn(**block["input"])
                    except Exception as e:
                        tool_output = f"Error: {e}"
                        logger.exception(f"[{self.name}] tool '{block['name']}' failed")

                # Serialize
                if not isinstance(tool_output, str):
                    tool_output = json.dumps(tool_output, indent=2, default=str)

                # --- Post-tool hooks ---
                for hook in self._hooks:
                    hook.post_tool_use(tool_call, tool_output)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": tool_output,
                })
                result.tool_calls.append({
                    "tool": block["name"],
                    "input": block["input"],
                    "result": tool_output[:500],
                })

                self._tracer.log_event("tool_call_end", {
                    "tool": block["name"],
                    "result_preview": tool_output[:200],
                })

                logger.info(f"[{self.name}] called {block['name']} -> {tool_output[:100]}")

            # Feed tool results back
            messages.append({"role": "user", "content": tool_results})

        result.messages = messages
        result.trace_file = self._tracer.end_run(result)
        return result
