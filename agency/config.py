"""Agent configuration — parse YAML config files into typed Python objects.

Each client deployment has a config.yaml that specifies which agent to run,
which connectors to use, and client-specific settings. This module loads
and validates those configs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgentConfig:
    """Configuration for a deployed agent instance."""

    # Identity
    name: str = ""
    company_name: str = ""
    language: str = "en"

    # Model
    model: str = "claude-sonnet-4-6"

    # Behavior
    max_iterations: int = 20
    min_invoice_threshold: float = 50.0
    high_value_threshold: float = 10_000.0

    # HITL
    hitl_mode: str = "full"  # full | exceptions_only | summary
    auto_approve_stages: list[str] = field(default_factory=list)

    # Schedule
    chase_schedule: list[int] = field(default_factory=lambda: [3, 7, 14, 30])

    # Connectors (which implementation to use)
    accounting: str = "xero"
    email: str = "gmail"
    messaging: str = "telegram"

    # Extra settings (catch-all for workflow-specific stuff)
    extra: dict[str, Any] = field(default_factory=dict)


def load_config(path: str | Path) -> AgentConfig:
    """Load an AgentConfig from a YAML file.

    Example config.yaml:
        name: "ar-follow-up"
        company_name: "ABC Distribution BV"
        language: nl
        model: claude-sonnet-4-6
        accounting: exact_online
        hitl_mode: full
        chase_schedule: [3, 7, 14, 30]
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Split known fields from extra
    known_fields = {f.name for f in AgentConfig.__dataclass_fields__.values()}
    known = {k: v for k, v in raw.items() if k in known_fields}
    extra = {k: v for k, v in raw.items() if k not in known_fields}

    config = AgentConfig(**known)
    config.extra = extra
    return config
