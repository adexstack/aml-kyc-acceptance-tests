# Regression runs and cross-run comparison — implementation findings

**Status: built 2026-07-31**, in the form phased by
[`../plan.md`](../plan.md) §R0–R2 rather than exactly as sketched in §2 below
(run identity travels as `release` *and* metadata, not metadata alone — see
the correction in §1). Written
2026-07-30 against `langfuse==4.14.1` as installed, and against a running
application instance's `/openapi.json` and live responses.

**Read [`regression-testing-plan.md`](regression-testing-plan.md) first.** That
doc decides *what a regression means* here (three causes to separate, why a
tolerance band must precede any CI gate, why a bespoke dashboard is
net-negative). This doc does not revisit any of it. This is only the
narrower question the user asked: given the 14 metrics already ported into
`experiments/*.py` and running through `run_experiment.py`, **what is the
simplest thing that makes runs comparable across runs and across metrics?**

Everything below was verified by inspecting the installed SDK and calling
the live API, not from the hosted docs — the two Langfuse pages consulted
([data model](https://langfuse.com/docs/observability/data-model),
[best practices](https://langfuse.com/docs/observability/best-practices))
do not document score-level filtering at all, and the data-model page does
not cover scores or dataset runs.

---

## 1. What the current implementation already does, and the one thing it lacks

`run_experiment.py` runs each of the 14 metrics as a separate
`langfuse.run_experiment(...)` call, producing 17 distinct score names
(some metrics emit two — e.g. `tool_correctness_names` and
`tool_correctness_arguments`).

Each score already carries, without any change: `name`, `value`, `comment`
(the judge's reason), `timestamp`, and `environment`.

**The gap is not storage — it's that nothing on a stored score says which
run it belongs to or what version of the application produced it.** So
"compare run A to run B" currently means reading timestamps in a UI and
trusting that you remember which run was which. That is the whole problem,
and it is much cheaper to fix than it looks.

### Two SDK facts that determine the design

Both confirmed by reading `langfuse==4.14.1` source, because both contradict
what you would reasonably assume:

1. **`environment` cannot be used to label a run.** `Langfuse(environment=…)`
   and `LANGFUSE_TRACING_ENVIRONMENT` exist, but `client.py:2980`
   **hardcodes** every experiment span's environment to the constant
   `"sdk-experiment"`, overriding whatever you pass. Any design that tags
   runs by environment silently does nothing. (This also means the
   `Environment = sdk-experiment` filter documented in `plan.md` §1.7 is not
   a convention this repo chose — it is imposed by the SDK.)

   **Measured 2026-07-31, and stranger than the source suggests:** spans and
   scores report *different* environments. `observations` reports
   `sdk-experiment`; `scores-numeric` and the UI's own score export report
   `default`. So filtering scores by `sdk-experiment` in the UI returns
   nothing at all. Filter on neither.

2. **Score `metadata` is a free-form dict that survives the round trip.**
   `Evaluation(metadata=…)` → `create_score(metadata=…)` (`client.py:3132`)
   → returned in the `metadata` field of every score read back via
   `scores_v3.get_many_v3()`. No evaluator in `experiments/*.py` currently
   passes it. **This is the cheapest possible place to put run identity.**

> **Correction, 2026-07-31 — metadata is necessary but not sufficient.**
> Score `metadata` is returned by the raw score API, so it works for the
> script in §2 below. It is **not** a dimension in the Metrics API or Custom
> Dashboards, so you **cannot group or break down by it in any Langfuse
> chart**. For visualisation, run identity must additionally travel as
> `release` (surfacing as the groupable `traceRelease` dimension). See
> [`plan.md`](../plan.md) §1 for the full dimension list and §2 (Phase R0.1)
> for the resulting "label in three places" design. Do not implement §2
> below on its own if you intend to build dashboards.

> **Two further corrections from building it, 2026-07-31 — both verified
> against the live API, and both silent failures if you get them wrong.**
>
> 1. **`get_many_v3` returns `metadata: None` unless you ask for it.** The v3
>    score API returns only core fields by default; `comment`, `config_id`
>    and `metadata` come from the `details` field group. So
>    `get_many_v3(...)` without `fields="details"` returns every score with
>    no run identity at all, and a comparison script built on it reports
>    "no labelled scores found" while the scores sit there perfectly well
>    stored. `compare_runs.py` passes `fields="details"`.
> 2. **Metadata does not round-trip verbatim.** The flat strings written on
>    the way in come back parsed: `"true"` returns as `True`, `"8"` as `8`,
>    `"0.0"` as `0`. Writing flat strings is still right (values are
>    flattened and serialised on export), but any reader must normalise
>    before comparing, or an axis will appear to differ from itself.

### The read path that makes comparison automatable

`LangfuseAPI.scores_v3.get_many_v3()` filters server-side by `name`,
`environment`, `from_timestamp`/`to_timestamp`, `value_min`/`value_max`,
`trace_id`, and `experiment_id`, returning records with
`['id','name','source','timestamp','environment','comment','metadata','value',…]`.

So comparison does not need a dashboard, a local score store, or Langfuse
Datasets. It needs one query per score name plus a client-side group-by.

`ExperimentResult.experiment_id` is also exposed (and is a `get_many_v3`
filter). For local — non-Dataset — items it is a random id generated per
`run_experiment()` call, so it is a precise join key but only if you record
it as it is produced; it is not derivable after the fact.

---

## 2. Recommended implementation — three changes, all test-side

This is bucket 1 (test-side, valuable, in scope). Total surface: one new
function, 17 one-line edits, one new script. No notebook changes, no new
network dependency in the offline suite, no application change.

### Change 1 — `experiments/common.py`: a `run_context()` helper

One function returning a flat `dict[str, str]` of run identity plus the
application version fingerprint:

| Key | Source | Why |
|---|---|---|
| `run_label` | `AML_RUN_LABEL`, defaulting to a UTC timestamp | The grouping key. Set it explicitly (`before-planner-fix`) and comparison becomes readable |
| `harness_sha` | `git rev-parse --short HEAD` of this repo | Distinguishes "the test changed" from "the app changed" |
| `judge_model` | existing `JUDGE_MODEL` | A judge swap invalidates comparison — `judge-calibration.md` |
| `seed_version` | existing seed guard | Environment drift, per `regression-testing-plan.md` §2 |
| `app_model` | `GET /api/agent/trace/{run_id}` → `model` | Application fingerprint |
| `app_prompt_version` | same → `prompt_version` | The dimension most likely to explain a real regression |
| `app_temperature` | same → `model_configuration.temperature` | A config change looks exactly like a quality change without this |

Values must be flat strings — metadata is flattened and serialised on export
(`_flatten_and_serialize_metadata_values`).

**Fail loudly, don't infer** (per `CLAUDE.md`): if the trace endpoint doesn't
serve a fingerprint field, record the literal string `"unavailable"` rather
than omitting the key or guessing. A comparison that silently lost its
version fingerprint is worse than one that says it doesn't have it.

### Change 2 — stamp it on every score, and name the run

17 mechanical edits across `experiments/{investigate,rag_query,retrieval,conversational}.py`:

```python
return Evaluation(name="answer_relevancy", value=metric.score,
                  comment=metric.reason, metadata=run_context())
```

And in `run_experiment.py`, pass the label into the run name so the UI is
navigable by something meaningful instead of a bare timestamp:

```python
result = langfuse.run_experiment(
    name=spec["name"],
    run_name=f"{spec['name']} - {run_label}",   # else defaults to "{name} - {iso_timestamp}"
    ...
    metadata={"schema_version": "1.0.0", "judge_model": JUDGE_MODEL,
              "run_label": run_label},
)
print(f"experiment_id={result.experiment_id}  run_name={result.run_name}")
```

Printing `experiment_id` matters: it is the only precise per-run join key,
it appears nowhere by default, and it cannot be reconstructed later.

### Change 3 — `compare_runs.py`, a new read-only script

Sibling of `run_experiment.py`, importing nothing from the notebooks. For
each of the 17 score names: `get_many_v3(name=…, from_timestamp=…)`, group by
`metadata["run_label"]`, print a table of score per label per metric, the
delta against the previous label, and — next to it — whether
`app_prompt_version` / `app_model` / `judge_model` changed between those
labels.

That last column is the entire point. `regression-testing-plan.md` §2 is
explicit that a score delta without a version fingerprint beside it "is just
a number that moved."

It should also append one JSONL row per score to `results/scores.jsonl`
(already gitignored). That is `regression-testing-plan.md` Phase 0 —
persisting what you already compute — which that doc says to do *regardless*
of Langfuse, and it gives a `grep`-able offline artifact that needs no
dashboard login.

**Cost: zero judge calls.** It is a read-only API query. It can be run as
often as you like, which is exactly why it should be a separate script from
the thing that costs money.

---

## 3. How to actually execute a regression comparison

```bash
# checkpoint A
AML_RUN_LABEL=baseline-2026-07-30 AML_RESET_BEFORE_RUN=true \
  uv run python run_experiment.py

# ... change something in the application ...

# checkpoint B
AML_RUN_LABEL=after-planner-change AML_RESET_BEFORE_RUN=true \
  uv run python run_experiment.py

# compare — costs nothing
uv run python compare_runs.py --since 7d
```

`AML_RESET_BEFORE_RUN=true` is **not optional for a comparison run.** The
planner skips tools already completed for a case, so the five tool-planning
metrics score differently on an un-reset instance for reasons that have
nothing to do with quality. Comparing a reset run to an un-reset one
produces a delta that means nothing. Note this performs one reset *per
metric* (`experiments/common.maybe_reset()`), and that reset **drops and
recreates every table** on the target instance — the live-application
constraint in `CLAUDE.md` applies in full.

**Cadence opinion:** per meaningful checkpoint, not per commit and not
nightly-by-default. A full 14-metric run costs the application's own LLM
calls plus roughly a hundred judge calls at `gpt-5.4-mini`
(`observability-plan.md` §1.6). Two labelled runs around a specific change
tell you more than seven unlabelled nightly ones, because with a label you
know what the change *was*.

---

## 4. What this deliberately does not do

### It does not gate CI on score deltas

`judge-calibration.md` Phase 1 — the noise-band measurement — still does not
exist. Without it there is no defensible threshold for "this drop is real,"
and `regression-testing-plan.md` §4 is explicit that a gate built before that
band exists will fire on ordinary judge variance and be disabled within a
month. `compare_runs.py` **reports**; a human judges. Revisit gating only
after the band is measured.

### It does not adopt Langfuse Datasets

Real Langfuse-hosted Datasets would give the native run-vs-run comparison UI
and populate `dataset_run_url` (currently always `None` here — `plan.md`
§1.7). Genuinely better, and correctly bucket 1. Deferred because:

- it requires uploading the 8 seed scenarios as **durable stored objects** in
  Langfuse, which is a different PII decision from streaming trace payloads
  through a mask. Synthetic data makes it defensible, but
  `observability-plan.md` §5's standard says that gets an explicit call-out
  and a data-protection sign-off, not an assumption;
- it changes data loading in all 14 experiment modules;
- the metadata approach above delivers the comparison itself at a fraction
  of the cost. Reach for Datasets when *someone other than the person who ran
  the suite* needs to browse comparisons in a UI — that is the honest trigger,
  same as `regression-testing-plan.md` §3 Phase 3.

### It does not build a dashboard

`regression-testing-plan.md` §4 already ruled this out. `compare_runs.py`
prints a table. Langfuse is the dashboard if you want one.

---

## 5. Application-side: what is and isn't needed (bucket 2)

**Verified live, 2026-07-30, against a running instance.**

### Nothing is required for the recommended implementation

`GET /api/agent/trace/{run_id}` already serves the full fingerprint. Confirmed
by calling it:

```
model:               "openai/gpt-4o-mini"
prompt_version:      "investigation_v1"
model_configuration: {"requested_model":"openai/gpt-4o-mini","temperature":0.0,
                      "max_retries":1,"timeout_s":60.0, ...}
latency_ms:          7594
created_at:          "2026-07-29T21:17:23.279587"
```

`regression-testing-plan.md` §2's claim that this needs no application change
is correct and now verified end to end. **Everything in §2 above can be built
without asking the application team for anything.**

### One genuine bucket-2 gap: no build/release identifier — **closed 2026-07-31**

**Asked for, delivered and verified the same day.** `GET /api/health` now
returns `build_version` (string, `"<version>+<short SHA>"`), documented in
`/openapi.json`; `schema_version` moved to `1.1.0` and the bump was confirmed
additive field-by-field. The deployed image still reports the documented
`+unknown` fallback, so the value is currently a constant and
`run_context()` records `"unavailable"` for it — a deployment gap, not an
application one. Full record:
[`asks/build-version-request.md`](asks/build-version-request.md).

The original statement of the gap follows, because it is why the field
exists.

The application exposes no version of *itself*. `/api/health` returns only
`{status, schema_version, eval_tracing}`; `schema_version` is an API contract
version, not a build. `prompt_version` and `model` are good proxies but they
only move when the prompt or model moves — a change to retrieval logic, a
tool implementation, or scoring thresholds would regress scores while every
fingerprint field stayed identical, and the comparison would show a delta
with "no version change" beside it. That is precisely the misattribution
`regression-testing-plan.md` §2 warns about.

**What to ask for, and stop there** (per `CLAUDE.md`'s bucket-2 rule — do not
design the application-side implementation from this repository): a build or
commit identifier on an existing documented endpoint, e.g. a `build_version`
or `git_sha` field added to `GET /api/health`. Small, read-only, no new
endpoint required. `run_context()` should pick it up and record
`"unavailable"` until it exists.

### Unrelated but material: `PlanAdherenceMetric` is unblocked

Not part of this design, but found while verifying the above. The live
instance reports:

```
/api/health         -> {"eval_tracing": true, ...}
/api/agent/trace/1  -> deepeval_trace is NOT null
```

**Confirmed unblocked 2026-07-31.** `README.md`, `metric-notes.md`,
`observability-plan.md` §3.3 and `regression-testing-plan.md` have been
corrected; the expected result is 14/14, not 13/14. `metric-notes.md` now
carries the current implementation note — in particular that the declared
plan is composed from the trace's *ex-ante* fields (`tools_selected[]`,
`skipped_tools[]`, `retrieval_runs[].query`, each with the agent's stated
reason) rather than from the executed span tree, which would compare the
trace with itself.

**Carry this into the regression design:** don't fold it into a comparison
spanning the unblock. A metric that just changed from never-running to
running shows up as a dramatic "improvement" that is really just a metric
coming online. Start its history fresh.

---

## 6. Honest summary

**Do this:** `run_context()` in `common.py`, `metadata=run_context()` on 17
evaluators, `run_name` from the label, and a read-only `compare_runs.py` that
groups scores by label and shows the version fingerprint beside every delta.
Comparison becomes a query instead of a UI hunt, costs no judge calls, and
needs nothing from the application.

**~~Ask the application for:~~ done** — `build_version` landed on
`GET /api/health` on 2026-07-31. It reports its `+unknown` fallback on the
deployed image, so until that image is built with its commit metadata the
original caveat still applies in practice: some real regressions remain
indistinguishable from judge noise.

**Don't do yet:** CI gating on deltas (needs `judge-calibration.md`'s noise
band first); Langfuse Datasets (needs a data-protection call, and the trigger
is a second person needing the UI).

**Resolved since first draft:** `PlanAdherenceMetric` is unblocked
(2026-07-31) and the four docs that said otherwise have been corrected.
Keep its score history separate from any pre-unblock baseline.
