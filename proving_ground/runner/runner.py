"""ProveRunner — Orchestrates scenario execution with multi-run and eval aggregation.

This is the main entry point for running proving ground tests. It handles:
- Single scenario runs
- Multi-run statistical aggregation
- Baseline comparison for regression detection
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from proving_ground.scenarios.engine import ScenarioEngine, ScenarioResult
from proving_ground.scenarios.loader import load_scenario
from proving_ground.evals.statistical import StatisticalResult, aggregate, save_baseline, load_baseline
from proving_ground.evals.regression import RegressionReport, detect_regression

logger = logging.getLogger(__name__)


class ProveRunner:
    """Orchestrates scenario runs with statistical aggregation and regression detection.

    Usage:
        runner = ProveRunner()
        result = runner.run_scenario("scenario_packs/ar_basic.yaml")
        stats = runner.run_statistical("scenario_packs/ar_basic.yaml", num_runs=5)
        report = runner.run_with_baseline("scenario_packs/ar_basic.yaml", baseline="baselines/ar_basic.json")
    """

    def __init__(
        self,
        default_model: str = "claude-haiku-4-5-20251001",
        scenarios_dir: str | Path | None = None,
        baselines_dir: str | Path | None = None,
    ):
        self._default_model = default_model
        self._scenarios_dir = Path(scenarios_dir or Path(__file__).parent.parent / "scenario_packs")
        self._baselines_dir = Path(baselines_dir or Path(__file__).parent.parent / "baselines")

    def run_scenario(self, scenario_path: str | Path) -> ScenarioResult:
        """Run a single scenario and return the result."""
        path = self._resolve_path(scenario_path)
        engine = ScenarioEngine(default_model=self._default_model)
        return engine.run(path)

    def run_statistical(
        self,
        scenario_path: str | Path,
        num_runs: int = 3,
    ) -> StatisticalResult:
        """Run a scenario N times and aggregate statistics."""
        path = self._resolve_path(scenario_path)
        results: list[ScenarioResult] = []

        for i in range(num_runs):
            logger.info(f"[Runner] Run {i + 1}/{num_runs}")
            engine = ScenarioEngine(default_model=self._default_model)
            result = engine.run(path)
            results.append(result)

        return aggregate(results)

    def run_with_baseline(
        self,
        scenario_path: str | Path,
        num_runs: int = 3,
        baseline_path: str | Path | None = None,
        update_baseline: bool = False,
    ) -> tuple[StatisticalResult, RegressionReport | None]:
        """Run statistical evaluation and compare against baseline.

        Args:
            scenario_path: Path to the scenario YAML.
            num_runs: Number of runs for statistical aggregation.
            baseline_path: Path to baseline JSON. Auto-resolved if None.
            update_baseline: If True, save current results as the new baseline.
        """
        stats = self.run_statistical(scenario_path, num_runs=num_runs)

        # Resolve baseline path
        if baseline_path is None:
            baseline_path = self._baselines_dir / f"{stats.scenario_name}.json"
        baseline_path = Path(baseline_path)

        # Compare against baseline
        report = None
        if baseline_path.exists():
            baseline = load_baseline(baseline_path)
            report = detect_regression(stats, baseline)

        # Update baseline if requested
        if update_baseline:
            save_baseline(stats, baseline_path)
            logger.info(f"[Runner] Baseline saved to {baseline_path}")

        return stats, report

    def run_suite(self, scenario_dir: str | Path | None = None) -> list[ScenarioResult]:
        """Run all scenarios in a directory."""
        d = Path(scenario_dir) if scenario_dir else self._scenarios_dir
        results = []
        for yaml_file in sorted(d.glob("*.yaml")):
            logger.info(f"[Runner] Running {yaml_file.name}")
            result = self.run_scenario(yaml_file)
            results.append(result)
        return results

    def _resolve_path(self, path: str | Path) -> Path:
        path = Path(path)
        if path.exists():
            return path
        # Try relative to scenarios dir
        resolved = self._scenarios_dir / path
        if resolved.exists():
            return resolved
        # Try adding .yaml extension
        resolved = self._scenarios_dir / f"{path}.yaml"
        if resolved.exists():
            return resolved
        raise FileNotFoundError(f"Scenario not found: {path}")
