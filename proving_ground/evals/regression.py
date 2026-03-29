"""Regression Detection — Compare current results against saved baselines.

Flags regressions when pass rate or score drops below threshold,
and improvements when they increase.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from proving_ground.evals.statistical import StatisticalResult


@dataclass
class CheckDelta:
    """Comparison of a single check between current and baseline."""
    name: str
    baseline_pass_rate: float
    current_pass_rate: float
    delta_pass_rate: float
    baseline_mean_score: float
    current_mean_score: float
    delta_mean_score: float
    verdict: str  # "pass", "regression", "improvement"


@dataclass
class RegressionReport:
    """Comparison of current results against a baseline."""
    scenario_name: str
    verdict: str  # "pass", "regression_detected", "improved"
    overall_delta: float  # Change in overall pass rate
    check_deltas: list[CheckDelta] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Regression Report: {self.scenario_name}",
            f"Verdict: {self.verdict.upper()}",
            f"Overall pass rate delta: {self.overall_delta:+.1%}",
        ]
        if self.regressions:
            lines.append(f"\nRegressions ({len(self.regressions)}):")
            for r in self.regressions:
                lines.append(f"  - {r}")
        if self.improvements:
            lines.append(f"\nImprovements ({len(self.improvements)}):")
            for i in self.improvements:
                lines.append(f"  + {i}")
        if self.check_deltas:
            lines.append("\nPer-check:")
            for cd in self.check_deltas:
                symbol = {"pass": "=", "regression": "-", "improvement": "+"}[cd.verdict]
                lines.append(f"  [{symbol}] {cd.name}: {cd.baseline_pass_rate:.0%} -> {cd.current_pass_rate:.0%} (delta {cd.delta_pass_rate:+.0%})")
        return "\n".join(lines)


def detect_regression(
    current: StatisticalResult,
    baseline: dict,
    threshold: float = 0.10,
) -> RegressionReport:
    """Compare current results against a saved baseline.

    Args:
        current: Current statistical result.
        baseline: Loaded baseline JSON dict (from save_baseline).
        threshold: Minimum delta to flag as regression or improvement.
    """
    report = RegressionReport(
        scenario_name=current.scenario_name,
        verdict="pass",
        overall_delta=current.pass_rate - baseline.get("pass_rate", 0),
    )

    baseline_checks = baseline.get("per_check", {})

    for name, cs in current.per_check.items():
        bc = baseline_checks.get(name, {})
        bp = bc.get("pass_rate", 0)
        bs = bc.get("mean_score", 0)

        delta_p = cs.pass_rate - bp
        delta_s = cs.mean_score - bs

        if delta_p < -threshold:
            verdict = "regression"
            report.regressions.append(f"{name}: pass rate {bp:.0%} -> {cs.pass_rate:.0%}")
        elif delta_p > threshold:
            verdict = "improvement"
            report.improvements.append(f"{name}: pass rate {bp:.0%} -> {cs.pass_rate:.0%}")
        else:
            verdict = "pass"

        report.check_deltas.append(CheckDelta(
            name=name,
            baseline_pass_rate=bp,
            current_pass_rate=cs.pass_rate,
            delta_pass_rate=delta_p,
            baseline_mean_score=bs,
            current_mean_score=cs.mean_score,
            delta_mean_score=delta_s,
            verdict=verdict,
        ))

    if report.regressions:
        report.verdict = "regression_detected"
    elif report.improvements and not report.regressions:
        report.verdict = "improved"

    return report
