"""docs/observability-plan.md Phase 1: run the acceptance suite's DeepEval
metrics as Langfuse experiments, so each run is stored and comparable
instead of printed once and discarded (plan.md Phases 1.4-1.5).

Deliberately not imported by, and importing nothing from, the 14 notebooks
or run_all.py (docs/observability-plan.md §3.1) - this is a separate,
CI-facing path with its own dependency (`langfuse`), installed only via
`uv sync --extra langfuse`. The portable offline suite never depends on it.
Metric logic lives in experiments/{rag_query,retrieval,investigate,
conversational}.py, each a from-scratch port of the notebook of the same
metric name - see those modules and the notebooks themselves for the full
narrative and per-metric limitations. This file is only the CLI entrypoint
and Langfuse wiring.

Usage:
    uv sync --extra langfuse
    uv run python run_experiment.py                  # all 14 metrics
    uv run python run_experiment.py tool_correctness  # substring filter
    uv run python run_experiment.py --list

Requires in .env (see .env.example): AML_API_BASE_URL, OPENAI_API_KEY,
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL (EU region host,
confirmed 2026-07-29 by inspecting langfuse==4.14.1's own source for which
env vars it reads).

Data-handling constraint (docs/observability-plan.md §5, plan.md §1.1):
every experiment module refuses to run (via experiments.common.
load_scenarios()) unless the target instance serves the known synthetic
seed data. Do not bypass that guard to point this at a real-customer-data
instance without first revisiting the hosting decision in plan.md §0 and
Phase 2's note.

Masking: `task()` and every evaluator across experiments/*.py return and
receive REAL, unmasked data - that is what DeepEval scores, and a metric
that compares arguments or names (ToolCorrectnessMetric, ArgumentCorrectness
Metric, ...) would be silently broken if fed a masked placeholder instead
of the real value. Masking happens exactly once, at the Langfuse SDK's
export boundary, via `Langfuse(mask=mask)` below - it redacts span
attributes as they are serialised for transmission, after DeepEval has
already scored the real data. Do not add a second, manual masking pass in
experiment code; that would corrupt scoring rather than add safety. See
langfuse_mask.py.

Operational note: every investigate-based experiment
(experiments/investigate.py, experiments/conversational.py's ToolUse and
MultiTurnMCPUse) calls maybe_reset() itself, mirroring its source
notebook's own reset cell. With AML_RESET_BEFORE_RUN=true, running the full
suite performs one reset per such metric - slower than one shared reset,
but faithful to each notebook's independent assumption of first-run planner
behaviour. See experiments/common.maybe_reset().
"""

from __future__ import annotations

import argparse
import sys

from experiments.common import JUDGE_MODEL, MissingConfiguration, env
from langfuse_mask import mask

import experiments.conversational as conversational
import experiments.investigate as investigate
import experiments.rag_query as rag_query
import experiments.retrieval as retrieval

ALL_EXPERIMENTS = (
    rag_query.EXPERIMENTS
    + retrieval.EXPERIMENTS
    + investigate.EXPERIMENTS
    + conversational.EXPERIMENTS
)


def _build_client():
    from langfuse import Langfuse

    # Fail loudly on bad/missing credentials now, not deep inside the SDK's
    # background export machinery later. Langfuse(...) construction
    # succeeds silently even with blank keys - auth_check() is the SDK's
    # own documented way to verify credentials eagerly (it is explicitly
    # "blocking," which is exactly what a one-shot CLI script wants).
    env("LANGFUSE_PUBLIC_KEY", required=True)
    env("LANGFUSE_SECRET_KEY", required=True)

    # Constructed directly rather than via get_client(): confirmed by
    # inspecting the installed langfuse==4.14.1 package -
    # inspect.signature(get_client) accepts only `public_key`; `mask` is a
    # Langfuse.__init__-only parameter.
    langfuse = Langfuse(mask=mask)
    if not langfuse.auth_check():
        raise MissingConfiguration(
            "Langfuse rejected LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY - no project "
            "found for these credentials. Check they're copied correctly from the "
            "Langfuse Cloud EU project (plan.md Phase 1.1) and that LANGFUSE_BASE_URL "
            "points at the EU host, not the US default."
        )
    return langfuse


def run_one(langfuse, build_fn) -> tuple[bool, str]:
    """Build and run one metric's experiment. Returns (passed, detail)."""
    try:
        spec = build_fn()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the whole suite
        return False, f"{exc.__class__.__name__} while building: {exc}"

    try:
        result = langfuse.run_experiment(
            name=spec["name"],
            data=spec["data"],
            task=spec["task"],
            evaluators=spec["evaluators"],
            max_concurrency=1,  # each item performs real LLM/tool calls; see plan.md §1.4
            metadata={"schema_version": "1.0.0", "judge_model": JUDGE_MODEL},
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__} while running: {exc}"

    print(result.format())
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("filter", nargs="?", default="",
                        help="only run experiments whose function name contains this substring")
    parser.add_argument("--list", action="store_true",
                        help="list available experiments and exit, without running any")
    args = parser.parse_args()

    matching = [fn for fn in ALL_EXPERIMENTS if args.filter.lower() in fn.__name__.lower()]

    if args.list:
        print("Available experiments:")
        for fn in ALL_EXPERIMENTS:
            print(f"  {fn.__name__}")
        return 0

    if not matching:
        print(f"No experiments matched {args.filter!r}. Available:", file=sys.stderr)
        for fn in ALL_EXPERIMENTS:
            print(f"  {fn.__name__}", file=sys.stderr)
        return 1

    langfuse = _build_client()

    failures: list[tuple[str, str]] = []
    for fn in matching:
        print(f"--> {fn.__name__}", flush=True)
        passed, detail = run_one(langfuse, fn)
        if not passed:
            print(f"    FAIL  {detail}", flush=True)
            failures.append((fn.__name__, detail))
        print()

    langfuse.shutdown()  # required in short-lived processes or data is lost

    print(f"{len(matching) - len(failures)}/{len(matching)} experiments ran cleanly")
    for name, detail in failures:
        print(f"  FAILED  {name}: {detail}")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
