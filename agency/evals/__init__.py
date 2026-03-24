"""Eval framework — test agent behavior by reading traces.

Evals answer: "Is the agent doing its job well?"

Three levels:
1. Unit tests — deterministic logic (stage routing, date parsing)
2. LLM-as-judge — quality scoring (is this email well-written?)
3. End-to-end — full pipeline simulation

All evals read from trace files (JSONL) produced by the agent's JSONTracer.
This means you can evaluate any past run without re-running it.
"""

from agency.evals.runner import run_eval, EvalResult
