"""Comparison Workbench — Run agent A vs B on the same scenarios.

Supports comparing different models, prompt versions, tool sets, or
any other configuration parameter side-by-side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from proving_ground.scenarios.engine import ScenarioEngine, ScenarioResult


@dataclass
class RunConfig:
    """Configuration for one side of a comparison."""
    label: str
    model: str = "claude-haiku-4-5-20251001"


@dataclass
class ComparisonResult:
    """Side-by-side results from a comparison."""
    scenario_name: str
    configs: list[RunConfig] = field(default_factory=list)
    results: dict[str, ScenarioResult] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"Comparison: {self.scenario_name}", ""]

        # Header
        labels = [c.label for c in self.configs]
        lines.append(f"{'Metric':<25} " + " ".join(f"{l:>15}" for l in labels))
        lines.append("-" * (25 + 16 * len(labels)))

        # Rows
        def _row(metric: str, values: list[str]):
            lines.append(f"{metric:<25} " + " ".join(f"{v:>15}" for v in values))

        _row("Verdict", [
            "PASS" if self.results[c.label].passed else "FAIL"
            for c in self.configs
        ])
        _row("Iterations", [
            str(self.results[c.label].iterations)
            for c in self.configs
        ])
        _row("Cost (USD)", [
            f"${self.results[c.label].cost_usd:.4f}"
            for c in self.configs
        ])
        _row("Emails sent", [
            str(len(self.results[c.label].emails_sent))
            for c in self.configs
        ])

        # Per-eval comparison
        all_evals: set[str] = set()
        for r in self.results.values():
            for er in r.eval_results:
                all_evals.add(er.name)

        if all_evals:
            lines.append("")
            lines.append("Eval checks:")
            for eval_name in sorted(all_evals):
                values = []
                for c in self.configs:
                    r = self.results[c.label]
                    matched = [er for er in r.eval_results if er.name == eval_name]
                    if matched:
                        values.append("PASS" if matched[0].passed else "FAIL")
                    else:
                        values.append("N/A")
                _row(f"  {eval_name}", values)

        return "\n".join(lines)


class ComparisonWorkbench:
    """Run multiple configurations on the same scenario for comparison.

    Usage:
        bench = ComparisonWorkbench()
        result = bench.compare(
            "scenario_packs/ar_basic.yaml",
            configs=[
                RunConfig(label="haiku", model="claude-haiku-4-5-20251001"),
                RunConfig(label="sonnet", model="claude-sonnet-4-6"),
            ]
        )
        print(result.summary())
    """

    def compare(
        self,
        scenario_path: str | Path,
        configs: list[RunConfig],
    ) -> ComparisonResult:
        """Run the scenario with each configuration and compare."""
        from proving_ground.scenarios.loader import load_scenario

        base_config = load_scenario(scenario_path)
        comparison = ComparisonResult(
            scenario_name=base_config.name,
            configs=configs,
        )

        for rc in configs:
            engine = ScenarioEngine(default_model=rc.model)
            # Override model in the loaded config
            base_config.model = rc.model
            result = engine.run_config(base_config)
            comparison.results[rc.label] = result

            # Reset timeline for next run
            base_config.timeline.reset()

        return comparison
