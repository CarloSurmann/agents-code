"""CLI — Command-line interface for the Agent Proving Ground.

Usage:
    python -m proving_ground.runner.cli run ar_basic.yaml
    python -m proving_ground.runner.cli run ar_basic.yaml --model claude-sonnet-4-6
    python -m proving_ground.runner.cli stats ar_basic.yaml --runs 5
    python -m proving_ground.runner.cli stats ar_basic.yaml --runs 5 --update-baseline
    python -m proving_ground.runner.cli suite
    python -m proving_ground.runner.cli compare ar_basic.yaml --models haiku,sonnet
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure agents-code is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def cmd_run(args):
    """Run a single scenario."""
    from proving_ground.runner.runner import ProveRunner
    from proving_ground.runner.report import format_terminal, format_markdown, format_json

    runner = ProveRunner(default_model=args.model)
    result = runner.run_scenario(args.scenario)

    if args.format == "json":
        print(format_json(result))
    elif args.format == "markdown":
        print(format_markdown(result))
    else:
        print(format_terminal(result))


def cmd_stats(args):
    """Run statistical evaluation."""
    from proving_ground.runner.runner import ProveRunner

    runner = ProveRunner(default_model=args.model)
    stats, report = runner.run_with_baseline(
        args.scenario,
        num_runs=args.runs,
        update_baseline=args.update_baseline,
    )

    print(stats.summary())
    if report:
        print("\n" + report.summary())


def cmd_suite(args):
    """Run all scenarios in a directory."""
    from proving_ground.runner.runner import ProveRunner
    from proving_ground.runner.report import format_terminal

    runner = ProveRunner(default_model=args.model)
    results = runner.run_suite(args.dir)

    passed = sum(1 for r in results if r.passed)
    print(f"\n{'=' * 60}")
    print(f"SUITE: {passed}/{len(results)} scenarios passed")
    print(f"{'=' * 60}\n")

    for result in results:
        print(format_terminal(result))
        print()


def cmd_compare(args):
    """Compare multiple model configurations."""
    from proving_ground.runner.comparison import ComparisonWorkbench, RunConfig

    models = args.models.split(",")
    configs = [RunConfig(label=m.strip(), model=m.strip()) for m in models]

    bench = ComparisonWorkbench()
    result = bench.compare(args.scenario, configs)
    print(result.summary())


def main():
    # Load .env
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)

    parser = argparse.ArgumentParser(
        prog="prove",
        description="Agent Proving Ground — Battle-test agents before deployment",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    p_run = subparsers.add_parser("run", help="Run a single scenario")
    p_run.add_argument("scenario", help="Scenario YAML file (name or path)")
    p_run.add_argument("--model", default="claude-haiku-4-5-20251001", help="LLM model")
    p_run.add_argument("--format", choices=["terminal", "markdown", "json"], default="terminal")
    p_run.set_defaults(func=cmd_run)

    # --- stats ---
    p_stats = subparsers.add_parser("stats", help="Statistical evaluation (N runs)")
    p_stats.add_argument("scenario", help="Scenario YAML file")
    p_stats.add_argument("--runs", type=int, default=3, help="Number of runs")
    p_stats.add_argument("--model", default="claude-haiku-4-5-20251001")
    p_stats.add_argument("--update-baseline", action="store_true", help="Save as new baseline")
    p_stats.set_defaults(func=cmd_stats)

    # --- suite ---
    p_suite = subparsers.add_parser("suite", help="Run all scenarios")
    p_suite.add_argument("--dir", default=None, help="Scenario directory")
    p_suite.add_argument("--model", default="claude-haiku-4-5-20251001")
    p_suite.set_defaults(func=cmd_suite)

    # --- compare ---
    p_compare = subparsers.add_parser("compare", help="Compare models side-by-side")
    p_compare.add_argument("scenario", help="Scenario YAML file")
    p_compare.add_argument("--models", required=True, help="Comma-separated model names")
    p_compare.set_defaults(func=cmd_compare)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    args.func(args)


if __name__ == "__main__":
    main()
