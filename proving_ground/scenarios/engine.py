"""Scenario Engine — Orchestrates scenario execution.

Loads a scenario, sets up mock providers, configures the agent,
runs it, and evaluates the results. This is the core of the proving ground.

Usage:
    engine = ScenarioEngine()
    result = engine.run("scenario_packs/ar_basic.yaml")
    print(result.report())
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agency.agent import Agent, Hook
from agency.evals.runner import EvalResult, run_eval
from agency.tools.email.mock import MockProvider
from agency.tracing import read_trace

from proving_ground.channels.test_channel import TestChannel
from proving_ground.providers.accounting_mock import MockAccountingProvider
from proving_ground.providers.base import CompanyInfo
from proving_ground.scenarios.clock import SimulatedClock
from proving_ground.scenarios.loader import ScenarioConfig, load_scenario
from proving_ground.scenarios.state import WorldState
from proving_ground.scenarios.timeline import TimelineHook
from proving_ground.tracing.enhanced_tracer import EnhancedJSONTracer
from proving_ground.tracing.tracing_hook import EnhancedTracingHook

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    """Results from running a single scenario."""
    scenario_name: str
    passed: bool = False
    agent_output: str = ""
    iterations: int = 0
    cost_usd: float = 0.0
    trace_file: str | None = None
    eval_results: list[EvalResult] = field(default_factory=list)
    emails_sent: list[dict] = field(default_factory=list)
    hitl_decisions: list[dict] = field(default_factory=list)
    error: str | None = None

    def summary(self) -> str:
        lines = [
            f"Scenario: {self.scenario_name}",
            f"Verdict:  {'PASS' if self.passed else 'FAIL'}",
            f"Iters:    {self.iterations}",
            f"Cost:     ${self.cost_usd:.4f}",
            f"Emails:   {len(self.emails_sent)}",
        ]
        if self.error:
            lines.append(f"Error:    {self.error}")
        if self.eval_results:
            lines.append("Evals:")
            for er in self.eval_results:
                mark = "PASS" if er.passed else "FAIL"
                lines.append(f"  [{mark}] {er.name}: {er.details}")
        return "\n".join(lines)


class ScenarioEngine:
    """Loads, sets up, runs, and evaluates scenarios.

    Usage:
        engine = ScenarioEngine()
        result = engine.run("scenario_packs/ar_basic.yaml")

        # Or with a pre-loaded config:
        config = load_scenario("...")
        result = engine.run_config(config)
    """

    def __init__(
        self,
        default_model: str = "claude-haiku-4-5-20251001",
        channel: TestChannel | None = None,
        extra_hooks: list[Hook] | None = None,
    ):
        self._default_model = default_model
        self._channel = channel or TestChannel(default_action="approve")
        self._extra_hooks = extra_hooks or []

    def run(self, scenario_path: str | Path) -> ScenarioResult:
        """Load and run a scenario from a YAML file."""
        config = load_scenario(scenario_path)
        return self.run_config(config)

    def run_config(self, config: ScenarioConfig) -> ScenarioResult:
        """Run a pre-loaded scenario configuration."""
        result = ScenarioResult(scenario_name=config.name)

        try:
            # --- Setup world state ---
            clock = SimulatedClock(start=config.clock_start)
            world = WorldState(clock)

            # Accounting provider
            accounting = MockAccountingProvider(clock=clock)
            if config.invoices:
                accounting.seed(
                    invoices=config.invoices,
                    contacts=config.contacts,
                    company_info=config.company_info or CompanyInfo(name="Test Company"),
                )
            world.register_accounting(accounting)

            # Email provider
            email = MockProvider()
            world.register_email(email)

            # --- Build hooks ---
            tracer = EnhancedJSONTracer()
            tracing_hook = EnhancedTracingHook(tracer)

            hooks: list[Hook] = []

            # Timeline hook (fires scenario events at tool-call boundaries)
            if config.timeline.events:
                timeline_hook = TimelineHook(
                    timeline=config.timeline,
                    dispatch_fn=world.dispatch,
                    elapsed_fn=world.elapsed,
                    tracer_fn=tracer.log_scenario_event,
                )
                hooks.append(timeline_hook)

            hooks.extend(self._extra_hooks)
            hooks.append(tracing_hook)  # Always last

            # --- Build tools ---
            tools = [*accounting.as_tools(), *email.as_tools()]

            # --- Build agent ---
            model = config.model or self._default_model

            system_prompt = self._build_system_prompt(config)

            agent = Agent(
                name=f"prove-{config.name}",
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                hooks=hooks,
                tracer=tracer,
                max_iterations=config.max_iterations,
            )

            # --- Run agent ---
            task = config.task or self._default_task(config)
            logger.info(f"[Engine] Running scenario '{config.name}' with model {model}")
            agent_result = agent.run(task)

            result.agent_output = agent_result.output
            result.iterations = agent_result.iterations
            result.cost_usd = agent_result.cost_usd
            result.trace_file = agent_result.trace_file
            result.emails_sent = email.get_sent_emails()
            result.hitl_decisions = [
                {"action": d.action, "source": d.source, "text": d.message_text[:200]}
                for d in self._channel.get_decisions()
            ]

            # --- Run evals ---
            if result.trace_file:
                events = read_trace(result.trace_file)
                result.eval_results = self._run_evals(config, events, result)

            # --- Determine pass/fail ---
            if result.eval_results:
                result.passed = all(er.passed for er in result.eval_results)
            else:
                result.passed = result.iterations > 0 and result.error is None

        except Exception as e:
            result.error = str(e)
            result.passed = False
            logger.exception(f"[Engine] Scenario '{config.name}' failed: {e}")

        return result

    def _build_system_prompt(self, config: ScenarioConfig) -> str:
        company = config.company_info.name if config.company_info else "Test Company"
        return f"""You are an Accounts Receivable assistant for {company}.

Your job:
1. Call get_overdue_invoices to see what's overdue
2. For each overdue invoice, draft and send a chase email using send_email
3. Match the tone to the chase stage based on days overdue

Chase stages:
- 1-6 days: Friendly reminder. Casual tone.
- 7-13 days: Firm but warm. Ask for payment date.
- 14-29 days: Formal. Reference previous reminders.
- 30+ days: Final notice. Serious tone.

Rules:
- Always include: invoice number, amount, due date
- Write in the customer's language when specified
- Be respectful
- Never threaten legal action
"""

    def _default_task(self, config: ScenarioConfig) -> str:
        return (
            "Fetch all overdue invoices and send a chase email for each one. "
            "Match the tone to how many days overdue each invoice is. "
            "Write in the customer's language when specified."
        )

    def _run_evals(self, config: ScenarioConfig, events: list[dict], result: ScenarioResult) -> list[EvalResult]:
        """Run expected_outcomes as eval checks."""
        from agency.evals.runner import check_tool_was_called, check_no_errors, check_completed_within

        eval_results = []

        # Built-in checks from expected_outcomes
        for outcome in config.expected_outcomes:
            check_type = outcome.get("check")
            name = outcome.get("name", check_type)

            if check_type == "tool_was_called":
                er = run_eval(name, events, check_tool_was_called(outcome["tool"]))
                eval_results.append(er)

            elif check_type == "no_errors":
                er = run_eval(name, events, check_no_errors())
                eval_results.append(er)

            elif check_type == "completed_within":
                er = run_eval(name, events, check_completed_within(outcome["max_iterations"]))
                eval_results.append(er)

            elif check_type == "emails_sent_count":
                expected = outcome.get("min", 1)
                actual = len(result.emails_sent)
                passed = actual >= expected
                eval_results.append(EvalResult(
                    name=name,
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    details=f"Expected >= {expected}, got {actual}",
                ))

            elif check_type == "cost_under":
                max_cost = outcome["max_usd"]
                passed = result.cost_usd <= max_cost
                eval_results.append(EvalResult(
                    name=name,
                    passed=passed,
                    score=1.0 if passed else 0.0,
                    details=f"Cost ${result.cost_usd:.4f} vs max ${max_cost}",
                ))

            else:
                logger.warning(f"[Engine] Unknown check type: {check_type}")

        return eval_results
