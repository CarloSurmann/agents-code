# Agent Proving Ground

Battle-test agents before customer deployment. A controlled replica of real business environments where agents are evaluated against deterministic and non-deterministic scenarios with full observability, fault injection, and comparison workbenches.

## Quick Start

```bash
# Run a single scenario
python -m proving_ground.runner run ar_basic.yaml

# Run with markdown output
python -m proving_ground.runner run ar_basic.yaml --format markdown

# Run all scenarios
python -m proving_ground.runner suite

# Statistical evaluation (3 runs + save baseline)
python -m proving_ground.runner stats ar_basic.yaml --runs 3 --update-baseline

# Compare models side-by-side
python -m proving_ground.runner compare ar_basic.yaml --models claude-haiku-4-5-20251001,claude-sonnet-4-6
```

## Architecture

```
proving_ground/
├── providers/          # Mock providers (AccountingProvider ABC + mock)
├── scenarios/          # Scenario engine (clock, timeline, state, loader)
├── scenario_packs/     # YAML scenario definitions
├── evals/              # Enhanced evals (checks, statistical, regression)
├── eval_suites/        # YAML eval suite configs
├── runner/             # Unified runner + CLI
├── tracing/            # Enhanced tracer (full I/O, HITL, cost, latency)
├── channels/           # TestChannel (programmable HITL approval)
├── faults/             # Fault injection (profiles, injector hook)
├── fault_profiles/     # YAML fault configs
└── baselines/          # Saved statistical baselines for regression detection
```

## How It Works

1. **Scenario YAML** defines initial state (invoices, contacts), timeline events, and expected outcomes
2. **ScenarioEngine** sets up mock providers, configures the agent, runs it, and evaluates
3. **TestChannel** auto-approves HITL gates (or can be programmed per-tool)
4. **EnhancedJSONTracer** records every tool call with full I/O
5. **Evals** check business rules, cost, latency, and correctness
6. **Statistical aggregation** across N runs with confidence intervals
7. **Regression detection** against saved baselines

## Key Design Decisions

- **No modifications to agency/ framework** — proving ground sits alongside, not inside
- **Agents don't know they're being tested** — same tool signatures, same hook chain
- **SimulatedClock injected, not monkeypatched** — only mock providers use it
- **Faults are Hooks** — compose naturally with the existing HITL chain
- **All eval checks use existing signature** — compatible with agency/evals/runner.py
