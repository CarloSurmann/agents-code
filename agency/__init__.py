"""
Agency — Shared building blocks for all AI agent workflows.

Public API:
    Agent          — The core agent class (agentic loop + hooks)
    load_skills    — Load .md skill files into system prompt sections
    load_config    — Parse YAML deployment config
"""

from agency.agent import Agent
from agency.skills import load_skills
from agency.config import load_config

__all__ = ["Agent", "load_skills", "load_config"]
