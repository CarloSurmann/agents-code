"""Report Generation — Format results for terminal, markdown, or JSON."""

from __future__ import annotations

import json
from proving_ground.scenarios.engine import ScenarioResult
from proving_ground.evals.statistical import StatisticalResult
from proving_ground.evals.regression import RegressionReport


def format_terminal(result: ScenarioResult) -> str:
    """Format a single scenario result for terminal output."""
    return result.summary()


def format_statistical_terminal(stats: StatisticalResult) -> str:
    """Format statistical results for terminal output."""
    return stats.summary()


def format_regression_terminal(report: RegressionReport) -> str:
    """Format regression report for terminal output."""
    return report.summary()


def format_markdown(result: ScenarioResult) -> str:
    """Format a single scenario result as markdown."""
    lines = [
        f"## Scenario: {result.scenario_name}",
        "",
        f"**Verdict:** {'PASS' if result.passed else 'FAIL'}",
        f"**Iterations:** {result.iterations}",
        f"**Cost:** ${result.cost_usd:.4f}",
        f"**Emails sent:** {len(result.emails_sent)}",
    ]

    if result.error:
        lines.append(f"\n**Error:** {result.error}")

    if result.eval_results:
        lines.append("\n### Eval Results\n")
        lines.append("| Check | Result | Score | Details |")
        lines.append("|-------|--------|-------|---------|")
        for er in result.eval_results:
            mark = "PASS" if er.passed else "FAIL"
            lines.append(f"| {er.name} | {mark} | {er.score:.2f} | {er.details} |")

    if result.emails_sent:
        lines.append("\n### Emails Sent\n")
        for e in result.emails_sent:
            lines.append(f"- **To:** {e['to']} — {e['subject'][:60]}")

    return "\n".join(lines)


def format_json(result: ScenarioResult) -> str:
    """Format a single scenario result as JSON."""
    return json.dumps({
        "scenario": result.scenario_name,
        "passed": result.passed,
        "iterations": result.iterations,
        "cost_usd": result.cost_usd,
        "emails_sent": len(result.emails_sent),
        "error": result.error,
        "evals": [
            {"name": er.name, "passed": er.passed, "score": er.score, "details": er.details}
            for er in result.eval_results
        ],
    }, indent=2)
