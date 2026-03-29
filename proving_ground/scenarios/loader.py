"""Scenario Loader — Parse YAML scenario packs into executable objects.

A scenario YAML has four sections:
1. initial_state: clock, invoices, contacts, emails, company_info
2. timeline: events with relative timestamps
3. expected_outcomes: assertions to run after the agent
4. agent_config: model, max_iterations, task prompt, eval suite
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from proving_ground.providers.base import Invoice, Contact, CompanyInfo
from proving_ground.scenarios.timeline import Event, Timeline, parse_offset

logger = logging.getLogger(__name__)


@dataclass
class ScenarioConfig:
    """Parsed scenario definition."""
    name: str
    description: str = ""

    # Initial state
    clock_start: date | None = None
    invoices: list[Invoice] = field(default_factory=list)
    contacts: dict[str, list[Contact]] = field(default_factory=dict)
    company_info: CompanyInfo | None = None

    # Timeline
    timeline: Timeline = field(default_factory=Timeline)

    # Expected outcomes (check name -> params)
    expected_outcomes: list[dict[str, Any]] = field(default_factory=list)

    # Agent configuration
    model: str = "claude-haiku-4-5-20251001"
    max_iterations: int = 20
    task: str = ""
    eval_suite: str = ""
    fault_profile: str = ""


def load_scenario(path: str | Path) -> ScenarioConfig:
    """Load a scenario from a YAML file."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    name = data.get("name", path.stem)
    config = ScenarioConfig(
        name=name,
        description=data.get("description", ""),
    )

    # --- Parse initial_state ---
    state = data.get("initial_state", {})

    if "clock" in state:
        config.clock_start = date.fromisoformat(state["clock"])

    if "company_info" in state:
        ci = state["company_info"]
        config.company_info = CompanyInfo(
            name=ci.get("name", "Test Company"),
            vat_number=ci.get("vat_number", ""),
            email=ci.get("email", ""),
            phone=ci.get("phone", ""),
        )

    if "invoices" in state:
        for inv_data in state["invoices"]:
            due = inv_data.get("due_date")
            if isinstance(due, str):
                due = date.fromisoformat(due)
            config.invoices.append(Invoice(
                id=inv_data.get("id", inv_data.get("invoice_number", "")),
                invoice_number=inv_data["invoice_number"],
                customer_name=inv_data.get("customer_name", "Unknown"),
                customer_email=inv_data.get("customer_email", ""),
                customer_code=inv_data.get("customer_code", ""),
                amount_net=float(inv_data.get("amount_net", 0)),
                amount_gross=float(inv_data.get("amount_gross", inv_data.get("amount", 0))),
                currency=inv_data.get("currency", "EUR"),
                due_date=due,
                description=inv_data.get("description", ""),
                status=inv_data.get("status", "not_paid"),
                language=inv_data.get("language", "en"),
            ))

    if "contacts" in state:
        for code, contact_list in state["contacts"].items():
            config.contacts[code] = [
                Contact(
                    name=c.get("name", ""),
                    email=c.get("email", ""),
                    phone=c.get("phone", ""),
                    job_title=c.get("job_title", ""),
                    customer_code=code,
                )
                for c in contact_list
            ]

    # --- Parse timeline ---
    for event_data in data.get("timeline", []):
        offset = parse_offset(event_data["at"])
        config.timeline.add(
            offset=offset,
            action=event_data["action"],
            params=event_data.get("params", {}),
        )

    # --- Parse expected_outcomes ---
    config.expected_outcomes = data.get("expected_outcomes", [])

    # --- Parse agent_config ---
    agent = data.get("agent_config", {})
    config.model = agent.get("model", config.model)
    config.max_iterations = agent.get("max_iterations", config.max_iterations)
    config.task = agent.get("task", "")
    config.eval_suite = agent.get("eval_suite", "")
    config.fault_profile = agent.get("fault_profile", "")

    logger.info(f"[Loader] Loaded scenario '{name}': {len(config.invoices)} invoices, "
                f"{len(config.timeline.events)} timeline events, "
                f"{len(config.expected_outcomes)} expected outcomes")

    return config
