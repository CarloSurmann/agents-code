"""Statistical Scoring — Aggregate eval results across multiple runs.

Computes mean, standard deviation, confidence intervals, and pass rates
for each eval check across N runs of the same scenario. No external
dependencies — uses stdlib statistics module.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agency.evals.runner import EvalResult
from proving_ground.scenarios.engine import ScenarioResult


@dataclass
class CheckStats:
    """Statistics for a single eval check across runs."""
    name: str
    pass_rate: float
    mean_score: float
    std_score: float
    min_score: float
    max_score: float


@dataclass
class StatisticalResult:
    """Aggregated statistics from N runs of the same scenario."""
    scenario_name: str
    num_runs: int
    pass_rate: float  # Fraction of runs where ALL checks passed
    mean_cost_usd: float
    std_cost_usd: float
    mean_iterations: float
    mean_emails_sent: float
    confidence_interval_95: tuple[float, float] = (0.0, 0.0)
    per_check: dict[str, CheckStats] = field(default_factory=dict)
    raw_results: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        ci_lo, ci_hi = self.confidence_interval_95
        lines = [
            f"Statistical Report: {self.scenario_name}",
            f"Runs:           {self.num_runs}",
            f"Pass rate:      {self.pass_rate:.1%}",
            f"95% CI:         [{ci_lo:.1%}, {ci_hi:.1%}]",
            f"Mean cost:      ${self.mean_cost_usd:.4f} (std ${self.std_cost_usd:.4f})",
            f"Mean iters:     {self.mean_iterations:.1f}",
            f"Mean emails:    {self.mean_emails_sent:.1f}",
        ]
        if self.per_check:
            lines.append("\nPer-check breakdown:")
            for name, cs in self.per_check.items():
                lines.append(f"  {name}: pass={cs.pass_rate:.0%} score={cs.mean_score:.2f}±{cs.std_score:.2f}")
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "scenario_name": self.scenario_name,
            "num_runs": self.num_runs,
            "pass_rate": self.pass_rate,
            "confidence_interval_95": list(self.confidence_interval_95),
            "mean_cost_usd": self.mean_cost_usd,
            "std_cost_usd": self.std_cost_usd,
            "mean_iterations": self.mean_iterations,
            "mean_emails_sent": self.mean_emails_sent,
            "per_check": {
                name: {
                    "pass_rate": cs.pass_rate,
                    "mean_score": cs.mean_score,
                    "std_score": cs.std_score,
                }
                for name, cs in self.per_check.items()
            },
        }


def aggregate(results: list[ScenarioResult]) -> StatisticalResult:
    """Compute statistical summary from multiple scenario run results."""
    n = len(results)
    if n == 0:
        return StatisticalResult(scenario_name="empty", num_runs=0, pass_rate=0.0,
                                 mean_cost_usd=0, std_cost_usd=0, mean_iterations=0, mean_emails_sent=0)

    scenario_name = results[0].scenario_name

    # Overall pass rate
    passes = sum(1 for r in results if r.passed)
    pass_rate = passes / n

    # Cost stats
    costs = [r.cost_usd for r in results]
    mean_cost = statistics.mean(costs)
    std_cost = statistics.stdev(costs) if n > 1 else 0.0

    # Iteration stats
    iters = [r.iterations for r in results]
    mean_iters = statistics.mean(iters)

    # Email stats
    emails = [len(r.emails_sent) for r in results]
    mean_emails = statistics.mean(emails)

    # 95% confidence interval for pass rate (Wilson score)
    ci = _wilson_ci(passes, n, z=1.96)

    # Per-check stats
    check_names: set[str] = set()
    for r in results:
        for er in r.eval_results:
            check_names.add(er.name)

    per_check: dict[str, CheckStats] = {}
    for name in sorted(check_names):
        scores = []
        passed_count = 0
        for r in results:
            for er in r.eval_results:
                if er.name == name:
                    scores.append(er.score)
                    if er.passed:
                        passed_count += 1

        if scores:
            per_check[name] = CheckStats(
                name=name,
                pass_rate=passed_count / len(scores),
                mean_score=statistics.mean(scores),
                std_score=statistics.stdev(scores) if len(scores) > 1 else 0.0,
                min_score=min(scores),
                max_score=max(scores),
            )

    return StatisticalResult(
        scenario_name=scenario_name,
        num_runs=n,
        pass_rate=pass_rate,
        mean_cost_usd=mean_cost,
        std_cost_usd=std_cost,
        mean_iterations=mean_iters,
        mean_emails_sent=mean_emails,
        confidence_interval_95=ci,
        per_check=per_check,
    )


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score confidence interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return (max(0.0, center - spread), min(1.0, center + spread))


def save_baseline(result: StatisticalResult, path: str | Path) -> None:
    """Save a statistical result as a JSON baseline file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_json(), f, indent=2)


def load_baseline(path: str | Path) -> dict:
    """Load a baseline JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)
