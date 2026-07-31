"""plan.md Phase R2: compare labelled runs, and refuse when they are not
comparable.

Read-only. Zero judge calls, zero writes to the application - it queries
scores that `run_experiment.py` already stored and prints a table. That is
exactly why it is a separate script from the thing that costs money: run it
as often as you like.

The load-bearing part is the refusal, not the table. Two runs differ for two
very different reasons (plan.md §0):

  the APPLICATION changed  - prompt_version, model, model_configuration,
      build. The comparison is valid and this is the explanatory variable
      you were looking for. Shown beside every delta.
  the MEASUREMENT changed  - judge model, seed version, contract version,
      reset semantics, harness commit, scenario count. The instrument moved,
      not the thing measured. Comparing anyway produces numbers that look
      like results and measure nothing, so this script refuses and exits
      non-zero.

Usage:
    uv run python compare_runs.py --since 7d
    uv run python compare_runs.py --runs baseline-07-30 after-planner-change
    uv run python compare_runs.py --runs a b --force     # prints, labelled UNSAFE
    uv run python compare_runs.py --offline              # results/scores.jsonl only
    uv run python compare_runs.py --explain bias after-planner-change   # drill down

Requires LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL in
.env, except with --offline. Every score it reads is also appended to
results/scores.jsonl (gitignored), which is the grep-able artifact
docs/regression-testing-plan.md Phase 0 asks for and what --offline reads.

It does NOT decide whether a delta is a regression. `!!` means "worth a
look", not "failed": the judge-noise band (docs/judge-calibration.md Phase
1, plan.md §R6) does not exist yet, so there is no defensible threshold.
Report first; judge later.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from experiments.common import (API_BASE, APPLICATION_AXES, MEASUREMENT_AXES,
                                MissingConfiguration, NOT_APPLICABLE, UNAVAILABLE, env)

SCORES_PATH = Path(__file__).resolve().parent / "results" / "scores.jsonl"

# An eyeball aid, deliberately not a threshold. plan.md §R6 replaces this
# with a per-metric band measured against an unchanged application; until
# then a single global number is the honest placeholder, and nothing gates
# on it.
NOTABLE_DELTA = 0.05
NOTABLE_LATENCY_FRACTION = 0.20

_DURATION = re.compile(r"^(\d+)([mhdw])$")
_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(text: str) -> datetime:
    """Accept `7d`, `24h`, `90m`, `2w`, or an ISO-8601 date/datetime."""
    match = _DURATION.match(text.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return datetime.now(timezone.utc) - timedelta(seconds=amount * _SECONDS[unit])
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(
            f"Could not read {text!r} as a time window. Use 7d / 24h / 90m / 2w, or an "
            f"ISO-8601 timestamp."
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Reading scores
# ---------------------------------------------------------------------------

def subject_ids(subject) -> tuple[str | None, str | None]:
    """(trace_id, observation_id) from a score's `subject`.

    Experiment scores are attached to an observation, which carries its own
    `trace_id`; scores attached directly to a trace have no observation.
    Anything else (session, experiment) has neither, and returning None is
    the honest answer rather than reusing an id that means something else.
    """
    kind = getattr(subject, "kind", None)
    if kind == "observation":
        return getattr(subject, "trace_id", None), subject.id
    if kind == "trace":
        return subject.id, None
    return None, None


def build_client():
    """An authenticated Langfuse client, or a specific error about why not."""
    from langfuse import Langfuse

    env("LANGFUSE_PUBLIC_KEY", required=True)
    env("LANGFUSE_SECRET_KEY", required=True)
    langfuse = Langfuse()
    if not langfuse.auth_check():
        raise MissingConfiguration(
            "Langfuse rejected LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY - no project found "
            "for these credentials. Check LANGFUSE_BASE_URL points at the same region as "
            "the keys."
        )
    return langfuse


def fetch_scores(langfuse, from_timestamp: datetime) -> list[dict]:
    """Every score stored since `from_timestamp`, as plain dicts.

    Filtered client-side to scores carrying a `run_label` in metadata, i.e.
    the ones this harness stamped. Deliberately not filtered by
    `environment`: the SDK hardcodes every experiment span to
    "sdk-experiment", so it carries no information and filtering on it can
    only mislead.

    `fields="details,subject"` is load-bearing: the v3 score API returns only
    core fields by default, so without `details` every score comes back with
    `metadata=None` and `comment=None` - i.e. no run identity at all, and a
    comparison that silently finds nothing. `subject` adds the trace and
    observation ids, which are the drill-down keys (plan.md §R5): without
    them a row in results/scores.jsonl cannot be traced back to the item that
    produced it once Langfuse is out of reach. Verified against the live API
    2026-07-31.
    """
    scores, cursor = [], None
    while True:
        page = langfuse.api.scores_v3.get_many_v3(from_timestamp=from_timestamp,
                                                  limit=100, cursor=cursor,
                                                  fields="details,subject")
        for score in page.data:
            metadata = getattr(score, "metadata", None) or {}
            if not isinstance(metadata, dict) or "run_label" not in metadata:
                continue
            if not isinstance(getattr(score, "value", None), (int, float)):
                continue  # categorical/text scores are not comparable as deltas
            trace_id, observation_id = subject_ids(getattr(score, "subject", None))
            scores.append({
                "id": score.id,
                "name": score.name,
                "value": float(score.value),
                "timestamp": score.timestamp.isoformat() if score.timestamp else None,
                "comment": score.comment,
                "trace_id": trace_id,
                "observation_id": observation_id,
                "metadata": metadata,
            })
        cursor = page.meta.cursor
        if not cursor:
            break

    return scores


def read_offline_scores(from_timestamp: datetime) -> list[dict]:
    """The same shape, from results/scores.jsonl - so a comparison still
    works when Langfuse is unreachable, or from a CI artifact with no
    dashboard login."""
    if not SCORES_PATH.is_file():
        raise SystemExit(
            f"--offline needs {SCORES_PATH}, which does not exist yet. Run compare_runs.py "
            f"once online (it writes that file), or copy it from a run that did."
        )
    scores = []
    for line in SCORES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        score = json.loads(line)
        stamp = score.get("timestamp")
        if stamp and datetime.fromisoformat(stamp) < from_timestamp:
            continue
        scores.append(score)
    return scores


def persist(scores: list[dict]) -> int:
    """Append scores not already recorded. Returns how many were new."""
    SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
    known = set()
    if SCORES_PATH.is_file():
        for line in SCORES_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                known.add(json.loads(line).get("id"))
    fresh = [s for s in scores if s["id"] not in known]
    with SCORES_PATH.open("a", encoding="utf-8") as handle:
        for score in fresh:
            handle.write(json.dumps(score, default=str) + "\n")
    return len(fresh)


# ---------------------------------------------------------------------------
# Grouping and the comparability guard
# ---------------------------------------------------------------------------

def group_by_label(scores: list[dict]) -> tuple[dict, dict, dict]:
    """Returns (values, axes, first_seen) keyed by run label.

    `values[label][(experiment, metric)]` is a list because a metric may
    score several scenarios in one run; the table reports their mean and says
    how many. **The key is (experiment, metric), not metric.** A score name
    is not an identity on its own: `app_latency_ms` from
    aml-acceptance-tool-correctness times an investigation, and the same name
    from aml-acceptance-summarization times a RAG query. Keyed by name alone
    they produce a delta between two different operations, attributed to a
    "prompt_version change" that is really two different prompts for two
    different endpoints - a fabricated comparable of exactly the kind this
    script exists to refuse.

    Scores written before `experiment` was recorded fall back to
    "unavailable" and so still group by name alone, which is the old
    behaviour and the best that can be done with them.

    `axes[label][axis]` is the SET of values seen, so a label reused across
    two different configurations is visible rather than silently collapsed.

    Axis values are coerced back to strings here because Langfuse does not
    round-trip metadata verbatim: the flat strings run_context() writes come
    back parsed, so "true" returns as True and "8" as 8 (verified against the
    live API 2026-07-31). Comparing them as strings keeps one axis
    comparable with itself regardless of which side of that round trip the
    value came from.
    """
    values: dict = defaultdict(lambda: defaultdict(list))
    axes: dict = defaultdict(lambda: defaultdict(set))
    first_seen: dict = {}

    for score in scores:
        metadata = score["metadata"]
        label = metadata["run_label"]
        experiment = str(metadata.get("experiment") or UNAVAILABLE)
        values[label][(experiment, score["name"])].append(score["value"])
        for axis in MEASUREMENT_AXES + APPLICATION_AXES:
            axes[label][axis].add(str(metadata.get(axis, UNAVAILABLE)))
        stamp = score.get("timestamp")
        if stamp and (label not in first_seen or stamp < first_seen[label]):
            first_seen[label] = stamp
    return values, axes, first_seen


def axis_value(axes: dict, label: str, axis: str) -> str:
    """One label's value for one axis, or a MIXED marker when the same label
    was used for two runs with different settings."""
    seen = sorted(axes[label].get(axis, {UNAVAILABLE}))
    if len(seen) == 1:
        return seen[0]
    return "MIXED:" + "|".join(seen)


def check_comparable(axes: dict, labels: list[str]) -> list[str]:
    """The measurement axes that differ across the labels, or are mixed
    within one of them. Empty means the runs are comparable."""
    problems = []
    for axis in MEASUREMENT_AXES:
        if axis == "run_label":
            continue
        values = [axis_value(axes, label, axis) for label in labels]
        if len(set(values)) > 1 or any(v.startswith("MIXED:") for v in values):
            problems.append(axis)
    return problems


def print_refusal(axes: dict, labels: list[str], problems: list[str]) -> None:
    print(f"REFUSING TO COMPARE {' vs '.join(labels)}\n")
    reported = [a for a in MEASUREMENT_AXES if a != "run_label"]
    rows = {axis: [axis_value(axes, label, axis) for label in labels] for axis in reported}
    axis_width = max(len(a) for a in reported)
    cell_width = max(len(v) for values in rows.values() for v in values)
    for axis in reported:
        cells = "  ->  ".join(f"{v:<{cell_width}}" for v in rows[axis])
        print(f"  {axis:<{axis_width}}  {cells}   "
              f"{'CHANGED' if axis in problems else 'ok'}")
    print(
        "\nThese runs are not comparable: the measurement changed, not just the\n"
        "application. Any score delta would mix that change in with whatever the\n"
        "application actually did.\n"
    )
    if "reset_before_run" in problems:
        print(
            "  reset_before_run in particular: the planner skips tools already completed\n"
            "  for a case, so a reset run and an un-reset one produce different tool plans\n"
            "  for reasons unrelated to quality.\n"
        )
    print("Re-run with matching settings, or pass --force to print the deltas anyway\n"
          "(every row will be labelled UNSAFE).")


# ---------------------------------------------------------------------------
# The comparison table
# ---------------------------------------------------------------------------

def app_change(axes: dict, baseline: str, current: str) -> str:
    """What changed application-side between two labels - the column that
    makes a delta mean something. docs/regression-testing-plan.md §2: a
    score delta without a version fingerprint beside it is just a number
    that moved."""
    changes = []
    for axis in APPLICATION_AXES:
        if axis == "app_run_id":
            continue  # differs on every run by construction; not a change
        before, after = axis_value(axes, baseline, axis), axis_value(axes, current, axis)
        if before != after:
            changes.append(f"{axis.removeprefix('app_')} {before}->{after}")
    if changes:
        return ", ".join(changes)
    if axis_value(axes, current, "app_build") == UNAVAILABLE:
        return "no fingerprint change (build unknown)"
    return "no application change"


def notable(metric: str, baseline: float, current: float) -> bool:
    if metric.startswith("app_latency"):
        return baseline > 0 and abs(current - baseline) / baseline >= NOTABLE_LATENCY_FRACTION
    return abs(current - baseline) >= NOTABLE_DELTA


def shared_and_unshared(values: dict, labels: list[str]) -> tuple[list, dict]:
    """Split the (experiment, metric) keys into those every label scored and
    those only some did. Only the first group can honestly be compared."""
    per_label = {label: set(values[label]) for label in labels}
    shared = set.intersection(*per_label.values()) if per_label else set()
    unshared = {label: sorted(per_label[label] - shared) for label in labels}
    return sorted(shared), {k: v for k, v in unshared.items() if v}


def print_table(values: dict, axes: dict, labels: list[str], *, unsafe: bool) -> None:
    baseline, current = labels[0], labels[-1]
    shared, unshared = shared_and_unshared(values, labels)
    marker = " UNSAFE" if unsafe else ""

    print(f"baseline: {baseline}    current: {current}"
          + (f"    (+{len(labels) - 2} run(s) between)" if len(labels) > 2 else ""))

    width = max([len(m) for _, m in shared] + [len("metric")])
    experiments = sorted({experiment for experiment, _ in shared})
    for experiment in experiments:
        if len(experiments) > 1 or experiment != UNAVAILABLE:
            print(f"\n  {experiment}")
        print(f"{'metric':<{width}}  {'baseline':>10}  {'current':>10}  {'delta':>9}   "
              f"app change")
        for key in [k for k in shared if k[0] == experiment]:
            metric = key[1]
            before, after = values[baseline][key], values[current][key]
            b, a = sum(before) / len(before), sum(after) / len(after)
            flag = " !!" if notable(metric, b, a) else "   "
            print(f"{metric:<{width}}  {b:>10.3f}  {a:>10.3f}  {a - b:>+9.3f}{flag} "
                  f"{app_change(axes, baseline, current)}{marker}")

    if unshared:
        print("\nnot compared - scored in some runs but not all:")
        for label, keys in unshared.items():
            for experiment, metric in keys:
                other = [l for l in labels if l != label]
                sibling_experiments = {e for l in other for e, _ in values[l]}
                if experiment == UNAVAILABLE:
                    verdict = ("recorded before `experiment` was stamped, so a filtered "
                               "run cannot be told from a failed one")
                elif experiment in sibling_experiments:
                    verdict = (f"!! {experiment} DID run in {', '.join(other)}, so this "
                               f"score is missing - an incomplete run, not a change")
                else:
                    verdict = f"{experiment} was not run in {', '.join(other)}"
                print(f"  {metric:<{width}}  only in {label}   {verdict}")

    if any(experiment == UNAVAILABLE for experiment, _ in shared):
        print(
            "\nWARNING: some scores carry no `experiment`, so they were matched by name\n"
            "  alone. That is the pre-2026-07-31 storage format, and name alone is not an\n"
            "  identity: app_latency_ms from one experiment times an investigation and\n"
            "  from another times a RAG query. Re-run both labels to get keyed scores."
        )

    counts = {label: sum(len(v) for k, v in values[label].items() if k in shared)
              for label in labels}
    print("\nscores per run (shared metrics only): "
          + ", ".join(f"{label}={counts[label]}" for label in labels))
    if len(set(counts.values())) > 1:
        print("  !! the runs do not carry the same number of scores for the metrics they "
              "share.\n     A partially failed run looks exactly like a quality change - "
              "check before\n     reading the deltas.")
    if all(axis_value(axes, label, "app_build") == UNAVAILABLE for label in labels):
        print(
            "\nWARNING: app_build is 'unavailable' for every run compared, so application\n"
            "changes may be invisible to this attribution. A change to retrieval logic, a\n"
            "tool implementation or a threshold moves scores while model, prompt_version\n"
            "and temperature all stay identical. The application serves build_version on\n"
            "GET /api/health; 'unavailable' here means it reported its documented\n"
            "'+unknown' fallback, i.e. the image was built without git metadata. See\n"
            "docs/asks/build-version-request.md."
        )
    print("\n'!!' means worth a look, not failed: no judge-noise band exists yet "
          "(plan.md §R6).")


# ---------------------------------------------------------------------------
# Drill-down (plan.md §R5)
# ---------------------------------------------------------------------------

def langfuse_url(langfuse, score: dict) -> str | None:
    """Deep link to the score's own trace, focused on its observation."""
    trace_id = score.get("trace_id")
    if langfuse is None or not trace_id:
        return None
    url = langfuse.get_trace_url(trace_id=trace_id)
    if url and score.get("observation_id"):
        url = f"{url}?observation={score['observation_id']}"
    return url


def explain(scores: list[dict], metric: str, label: str, langfuse=None) -> int:
    """Collapse "a number moved" -> "here is the evidence" into one command.

    Prints, for each item scored by `metric` in run `label`: the score, the
    judge's own reason, the application fingerprint, and the two places to
    look next. The asymmetry between those two places is deliberate and
    worth keeping in mind while reading the output: **the application holds
    the unmasked detail and is local; the Langfuse copy is masked by
    design** (docs/observability-plan.md §5). Real debugging happens against
    the application.
    """
    rows = [s for s in scores
            if s["name"] == metric and s["metadata"].get("run_label") == label]
    if not rows:
        in_label = sorted({s["name"] for s in scores
                           if s["metadata"].get("run_label") == label})
        if not in_label:
            print(f"No scores stored for run label {label!r}. Known labels: "
                  f"{sorted({s['metadata'].get('run_label') for s in scores})}",
                  file=sys.stderr)
        else:
            print(f"Run {label!r} has no scores named {metric!r}. It scored: "
                  f"{in_label}", file=sys.stderr)
        return 1

    for score in sorted(rows, key=lambda s: (str(s["metadata"].get("item_scenario")),
                                             s["timestamp"] or "")):
        metadata = score["metadata"]
        run_id = metadata.get("app_run_id", UNAVAILABLE)
        scenario = metadata.get("item_scenario")
        print(f"\n{'=' * 78}\n{metric}   run={label}   score={score['value']:.3f}")
        print(f"  scenario     {scenario if scenario is not None else 'not recorded'}"
              f"   (scored {score['timestamp']})")
        print(f"  application  run_id={run_id}  model={metadata.get('app_model')}  "
              f"prompt={metadata.get('app_prompt_version')}  "
              f"temp={metadata.get('app_temperature')}  build={metadata.get('app_build')}")
        print(f"  measurement  judge={metadata.get('judge_model')}  "
              f"seed={metadata.get('seed_version')}  "
              f"contract={metadata.get('schema_version')}  "
              f"reset={metadata.get('reset_before_run')}  "
              f"harness={metadata.get('harness_sha')}")

        print("\n  judge reason")
        if score.get("comment"):
            for line in str(score["comment"]).strip().splitlines():
                print(f"    {line.strip()}")
        else:
            print("    (none - this metric records no reason, e.g. the app_latency_* "
                  "scores,\n     which are measurements rather than judgements)")

        print("\n  where to look next")
        if str(run_id) not in ("", UNAVAILABLE, NOT_APPLICABLE, "None"):
            print("    application - unmasked, local; this is where debugging happens:")
            print(f"      curl -s '{API_BASE}/api/agent/trace/{run_id}'")
            print("          step timeline, per-step latency_ms, tools_selected, "
                  "skipped_tools, tool_calls")
            print(f"      curl -s '{API_BASE}/api/eval/export/{run_id}'")
            print("          input, actual_output, tools_called")
        else:
            print("    application - no run id recorded for this score, so there is "
                  "nothing to fetch")
        url = langfuse_url(langfuse, score)
        if url:
            print("    langfuse - masked copy; item input/expected output, run metadata:")
            print(f"      {url}")
        elif score.get("trace_id"):
            print(f"    langfuse - trace_id={score['trace_id']} "
                  f"observation_id={score.get('observation_id')}")
            print("      (run without --offline for a clickable URL)")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="7d",
                        help="how far back to read scores (7d, 24h, 90m, 2w, or ISO-8601)")
    parser.add_argument("--runs", nargs="+", metavar="LABEL",
                        help="run labels to compare, oldest first; default is every label "
                             "found in the window")
    parser.add_argument("--force", action="store_true",
                        help="compare even when the measurement axes differ; every row is "
                             "labelled UNSAFE")
    parser.add_argument("--offline", action="store_true",
                        help="read results/scores.jsonl instead of querying Langfuse")
    parser.add_argument("--all", action="store_true",
                        help="compare every label in the window instead of the two most "
                             "recent")
    parser.add_argument("--list", action="store_true",
                        help="list the run labels in the window and exit")
    parser.add_argument("--explain", nargs=2, metavar=("METRIC", "LABEL"),
                        help="drill down: print the score, the judge's reason, the "
                             "application fingerprint and where to look next, for one "
                             "metric in one run (e.g. --explain bias before-planner-fix)")
    args = parser.parse_args()

    since = parse_since(args.since)
    langfuse = None if args.offline else build_client()
    try:
        scores = read_offline_scores(since) if args.offline else fetch_scores(langfuse, since)
        if not scores:
            print(f"No labelled scores found since {since.isoformat()}. Either nothing has "
                  f"run in that window, or the scores predate run_context() being stamped "
                  f"on them.")
            return 1

        if not args.offline:
            added = persist(scores)
            print(f"{len(scores)} score(s) read, {added} new appended to "
                  f"{SCORES_PATH.relative_to(Path.cwd()) if SCORES_PATH.is_relative_to(Path.cwd()) else SCORES_PATH}\n")

        if args.explain:
            return explain(scores, args.explain[0], args.explain[1], langfuse)
    finally:
        if langfuse is not None:
            langfuse.shutdown()

    values, axes, first_seen = group_by_label(scores)
    ordered = sorted(values, key=lambda label: first_seen.get(label, ""))

    if args.list:
        for label in ordered:
            print(f"{first_seen.get(label, '?')}  {label}  "
                  f"({sum(len(v) for v in values[label].values())} scores)")
        return 0

    if args.runs:
        labels = args.runs
    elif args.all:
        labels = ordered
    else:
        # Every label in the window is almost never the question being asked -
        # a week of runs of different metrics compared as one set is noise, and
        # it buries whichever two you actually cared about. Default to the two
        # most recent; --runs and --all are there for anything else.
        labels = ordered[-2:]
        if len(ordered) > 2:
            print(f"comparing the 2 most recent of {len(ordered)} labels in this window. "
                  f"Use --runs A B [C ...] to pick others, or --all for every label.\n"
                  f"  others: {', '.join(ordered[:-2])}\n")

    unknown = [label for label in labels if label not in values]
    if unknown:
        print(f"No scores found for run label(s) {unknown} in the window since "
              f"{since.isoformat()}. Known labels: {ordered}", file=sys.stderr)
        return 1
    if len(labels) < 2:
        print(f"Need at least two run labels to compare; found {labels}. Label a run with "
              f"AML_RUN_LABEL=... when you run run_experiment.py.", file=sys.stderr)
        return 1
    if args.runs:
        labels = sorted(labels, key=lambda label: first_seen.get(label, ""))

    problems = check_comparable(axes, labels)
    if problems and not args.force:
        print_refusal(axes, labels, problems)
        return 2
    if problems:
        print_refusal(axes, labels, problems)
        print("\n--force given: comparing anyway.\n")

    # Nothing in common is a different refusal from "the axes moved", and
    # --force cannot help: there is no shared measurement to print. This is
    # what comparing a tool_correctness run with a summarization run looks
    # like, and by name alone it would have produced an app_latency_ms delta
    # between an investigation and a RAG query.
    shared, _ = shared_and_unshared(values, labels)
    if not shared:
        print(f"REFUSING TO COMPARE {' vs '.join(labels)}\n")
        for label in labels:
            experiments = sorted({e for e, _ in values[label]})
            print(f"  {label:<24} ran {', '.join(experiments)}")
        print(
            "\nThese runs scored no metric in common, so there is nothing to compare.\n"
            "A score name is not an identity by itself: app_latency_ms from one\n"
            "experiment times an investigation and from another times a RAG query.\n"
            "Comparing them by name would be a delta between two different operations.\n"
            "\nRun the same experiment under both labels, e.g.\n"
            "  AML_RUN_LABEL=a uv run python run_experiment.py summarization\n"
            "  AML_RUN_LABEL=b uv run python run_experiment.py summarization"
        )
        return 2

    print_table(values, axes, labels, unsafe=bool(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
