"""Agency — Reusable agent framework with swappable LLM, channels, and tools.

Build agents by composing tools, skills, and hooks:

    from agency import Agent
    from agency.tools import gmail
    from agency.channels.telegram import TelegramChannel
    from agency.hooks.hitl import ChannelHITL
    from agency.tracing import JSONTracer

    agent = Agent(
        name="ar-follow-up",
        model="ollama/qwen3.5:9b",   # or "claude-sonnet-4-6" for prod
        tools=[gmail.send_email, gmail.search_inbox],
        skills=["draft-chase-email"],
        hooks=[ChannelHITL(channel=tg, chat_id="123", gated_tools=["send_email"])],
        tracer=JSONTracer(),
    )

    result = agent.run("Check for overdue invoices and draft chase emails.")
"""

from agency.agent import Agent, Hook, ToolCall, AgentResult
from agency.skills import load_skills
from agency.config import AgentConfig, load_config
from agency.tracing import JSONTracer, NullTracer, read_trace
