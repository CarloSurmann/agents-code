"""Fault Profiles — Declarative fault injection rules.

Defines what faults to inject, where, and with what probability.
Loaded from YAML or constructed programmatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class FaultRule:
    """A single fault injection rule."""
    target: str          # Tool name or "*" for all tools
    fault_type: str      # rate_limit, timeout, error, malformed, slow
    probability: float = 1.0  # 0.0 to 1.0
    after_n_calls: int = 0    # Only start faulting after N successful calls
    max_faults: int = 0       # 0 = unlimited
    params: dict[str, Any] = field(default_factory=dict)
    # params examples:
    #   rate_limit: {"message": "Rate limit exceeded", "retry_after": 60}
    #   timeout: {"seconds": 30}
    #   error: {"message": "Internal server error", "code": 500}
    #   malformed: {"corrupt_field": "amount", "corrupt_value": "NaN"}
    #   slow: {"delay_seconds": 5}


@dataclass
class FaultProfile:
    """A collection of fault rules for a test scenario."""
    name: str
    description: str = ""
    rules: list[FaultRule] = field(default_factory=list)

    def rules_for_tool(self, tool_name: str) -> list[FaultRule]:
        """Get all rules that apply to a specific tool."""
        return [
            r for r in self.rules
            if r.target == tool_name or r.target == "*"
        ]


def load_fault_profile(path: str | Path) -> FaultProfile:
    """Load a fault profile from YAML."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    profile = FaultProfile(
        name=data.get("name", Path(path).stem),
        description=data.get("description", ""),
    )

    for rule_data in data.get("rules", []):
        profile.rules.append(FaultRule(
            target=rule_data["target"],
            fault_type=rule_data["fault_type"],
            probability=rule_data.get("probability", 1.0),
            after_n_calls=rule_data.get("after_n_calls", 0),
            max_faults=rule_data.get("max_faults", 0),
            params=rule_data.get("params", {}),
        ))

    return profile
