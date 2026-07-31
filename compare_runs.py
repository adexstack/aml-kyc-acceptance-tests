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

from experiments.common import (APPLICATION_AXES, MEASUREMENT_AXES, MissingConfiguration,
                                UNAVAILABLE, env)

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


def fetch_scores(from_timestamp: datetime) -> list[dict]:
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

    langfuse.shutdown()
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

    `values[label][metric]` is a list because a metric may score several
    scenarios in one run; the table reports their mean and says how many.
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
        values[label][score["name"]].append(score["value"])
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


def print_table(values: dict, axes: dict, labels: list[str], *, unsafe: bool) -> None:
    baseline, current = labels[0], labels[-1]
    metrics = sorted({m for label in labels for m in values[label]})
    width = max([len(m) for m in metrics] + [len("metric")])
    marker = " UNSAFE" if unsafe else ""

    print(f"baseline: {baseline}    current: {current}"
          + (f"    (+{len(labels) - 2} run(s) between)" if len(labels) > 2 else ""))
    print(f"{'metric':<{width}}  {'baseline':>10}  {'current':>10}  {'delta':>9}   "
          f"app change")
    for metric in metrics:
        before = values[baseline].get(metric)
        after = values[current].get(metric)
        if not before or not after:
            missing = baseline if not before else current
            print(f"{metric:<{width}}  {'-':>10}  {'-':>10}  {'-':>9}   "
                  f"!! not scored in {missing} - an incomplete run, not a change{marker}")
            continue
        b, a = sum(before) / len(before), sum(after) / len(after)
        flag = " !!" if notable(metric, b, a) else "   "
        print(f"{metric:<{width}}  {b:>10.3f}  {a:>10.3f}  {a - b:>+9.3f}{flag} "
              f"{app_change(axes, baseline, current)}{marker}")

    counts = {label: sum(len(v) for v in values[label].values()) for label in labels}
    print("\nscores per run: " + ", ".join(f"{label}={counts[label]}" for label in labels))
    if len(set(counts.values())) > 1:
        print("  !! the runs do not carry the same number of scores. A partially failed "
              "run\n     looks exactly like a quality change - check before reading the "
              "deltas.")
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
    parser.add_argument("--list", action="store_true",
                        help="list the run labels in the window and exit")
    args = parser.parse_args()

    since = parse_since(args.since)
    scores = read_offline_scores(since) if args.offline else fetch_scores(since)
    if not scores:
        print(f"No labelled scores found since {since.isoformat()}. Either nothing has run "
              f"in that window, or the scores predate run_context() being stamped on them.")
        return 1

    if not args.offline:
        added = persist(scores)
        print(f"{len(scores)} score(s) read, {added} new appended to "
              f"{SCORES_PATH.relative_to(Path.cwd()) if SCORES_PATH.is_relative_to(Path.cwd()) else SCORES_PATH}\n")

    values, axes, first_seen = group_by_label(scores)
    ordered = sorted(values, key=lambda label: first_seen.get(label, ""))

    if args.list:
        for label in ordered:
            print(f"{first_seen.get(label, '?')}  {label}  "
                  f"({sum(len(v) for v in values[label].values())} scores)")
        return 0

    labels = args.runs or ordered
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

    print_table(values, axes, labels, unsafe=bool(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
