# Regression, observability and monitoring — phased implementation plan

**Status: R0, R1 and R2 are built (2026-07-31). R3–R6 are not.**
Written 2026-07-31. Supersedes the previous contents of this file, which
were the Phase 0/1 Langfuse bring-up and are archived verbatim at
[`docs/archive/Langfuse-observability-plan.md`](docs/archive/Langfuse-observability-plan.md)
— that work is **done** (14 metrics ported to `experiments/*.py`,
`run_experiment.py`, masking, CI workflow, self-hosted runner) and this plan
builds on it rather than repeating it.

**Read [`docs/regression_planning.md`](docs/regression_planning.md) first.**
It contains the SDK-level findings this plan is built on. Read
[`docs/regression-testing-plan.md`](docs/regression-testing-plan.md) for what
"regression" means here, and
[`docs/performance-latency-plan.md`](docs/performance-latency-plan.md) for
what latency can honestly be claimed from outside. This plan does not
re-argue any of it.

---

## 0. What "success" means, stated as testable conditions

The requested outcome, decomposed so each phase can be checked against it:

| # | Success condition | Delivered by |
|---|---|---|
| S1 | Compare **2+ metrics** across **2+ runs** in one view | R1, R3 |
| S2 | Catch degradation in **scores** | R1, R2, R3 |
| S3 | Catch degradation in **latency** | R1 (honest latency), R3 |
| S4 | Comparisons are **honest** — and when they are not comparable, the system **says so loudly and refuses**, rather than showing a misleading delta | **R2** — the load-bearing phase |
| S5 | **Drill down** from a moved number to the underlying evidence | R5 |
| S6 | Robust **monitoring** — a regression is noticed without someone watching a chart | R4 |

**S4 is the phase to protect if effort has to be cut.** A comparison tool
that silently compares incomparable runs is worse than no tool: it produces
numbers that look like results and measure nothing — exactly the failure mode
`CLAUDE.md` names under "Fail loudly, never infer."

### The distinction S4 depends on

Two different things make two runs differ, and conflating them is the whole
risk:

| Kind of difference | Comparison is | What to do |
|---|---|---|
| **App changed** — `prompt_version`, `model`, `model_configuration` | **Valid.** This is the explanatory variable you are *looking for* | Compare, and display the app delta beside the score delta |
| **Measurement changed** — `judge_model`, `seed_version`, `schema_version`, reset semantics, harness commit, scenario set | **Invalid / confounded.** The instrument moved, not the thing measured | **Refuse to compare. Shout.** |

A tool that treats these the same way will attribute a judge-model swap to
the application and destroy trust in the suite within a month.

---

## 1. Decision: Option 1 (Langfuse) is achievable — with one constraint that shapes everything

**Verdict: Option 1 (Langfuse for visualisation and monitoring) is viable
and is the recommendation.** Option 2 (a custom dashboard) is **not**
required and should not be built yet — see §8 for the specific triggers that
would change that.

Langfuse provides, natively:

- **Custom Dashboards** — line/bar/time-series/pie widgets over traces,
  observations and scores, with filtering and group-by; manageable via UI,
  a public API (`/api/public/unstable`), CLI, and an MCP server.
- **Metrics API v2** (`GET /api/public/v2/metrics`) — views `observations`,
  `scores-numeric`, `scores-categorical`; aggregations `sum`, `avg`, `count`,
  `max`, `min`, `p50/p75/p90/p95/p99`, `histogram`; time granularities from
  minute to month.
- **Monitors and Alerts** — threshold monitors over scores and observations,
  separate **warning** and **alert** thresholds, explicit **no-data
  handling**, routed to **Slack, webhooks, or GitHub Actions**
  (`workflow_dispatch`).

That covers S1, S2, S3, S5 and S6 without building any UI.

### The constraint: score `metadata` is **not** a dashboard dimension

This is the finding that dictates the design, and it is easy to get wrong.

`docs/regression_planning.md` §2 recommends stamping run identity into
`Evaluation(metadata=…)`. That is correct **for the raw score API**
(`scores_v3.get_many_v3()` returns `metadata`, so a script can group by it)
— but the **Metrics API and Custom Dashboards cannot group by metadata at
all.** Verified by reading the installed SDK's own `metrics()` docstring; the
`scores-numeric` view's complete dimension list is:

```
environment, name, source, dataType, configId, timestampMonth, timestampDay,
value, traceName, tags, traceRelease, traceVersion, observationName,
observationModelName, observationPromptName, observationPromptVersion
```

No `metadata`. **So metadata alone gets you a working script and a useless
dashboard.**

Two further constraints found the same way:

- **`environment` cannot carry run identity.** `client.py:2980` hardcodes
  every experiment span's environment to the constant `"sdk-experiment"`,
  overriding `Langfuse(environment=…)` and `LANGFUSE_TRACING_ENVIRONMENT`.
  Any design that labels runs by environment silently does nothing.

  **And it is worse than that — measured 2026-07-31.** Spans and scores do
  not even agree on the value:

  ```
  observations view : environment = "sdk-experiment"
  scores-numeric    : environment = "default"
  ```

  So a scores widget filtered to `sdk-experiment` returns **nothing at all**,
  and an observations widget filtered to `default` likewise. This is the
  concrete reason for §5's "do not add environment filters" rule — it is not
  merely uninformative, it silently empties the chart.
- **`observationPromptVersion` / `observationPromptName` come from Langfuse
  prompt *linking*,** not from arbitrary strings. Using them would mean
  adopting Langfuse prompt management for application prompts, which
  `observability-plan.md` §3.2 rules out on governance grounds. **Do not use
  these dimensions.**

### The resolution: `release` is the grouping key

`release` is applied as an **OpenTelemetry Resource attribute** on the
TracerProvider (`resource_manager.py:632-637`) and is **not** overridden the
way `environment` is. It surfaces as the **`traceRelease` dimension**, which
*is* groupable on the `scores-numeric` view.

So: **`release` = the run label.** Set once per process via
`Langfuse(release=…)` or `LANGFUSE_RELEASE`. Everything a dashboard needs to
group by must go there; everything else is metadata for scripts and
drill-down.

> **Verification gate before building R3: PASSED, runtime-verified
> 2026-07-31.** Three labelled runs (`verify-a/b/c`) were queried through the
> Metrics API itself, not just the UI:
>
> ```
> view=scores-numeric, dimensions=[traceRelease, name], metrics=[avg(value)]
> -> {'traceRelease': 'verify-a', 'name': 'app_latency_ms', 'avg_value': 9509}
>    {'traceRelease': 'verify-b', 'name': 'app_latency_ms', 'avg_value': 7684}
>    {'traceRelease': 'verify-c', 'name': 'tool_correctness_names', 'avg_value': 1}
>    ...
> ```
>
> `release` set on the client does reach `traceRelease`, and it groups. R3 is
> unblocked whenever someone decides a dashboard is worth having (§11.5).
>
> **The `traceName` fallback would not have worked**, so it was fortunate
> rather than redundant: every score in every run reports
> `traceName = "experiment-item-run"` (and the score export reports it as
> `null` outright). It is a constant, not a per-run key. Had the `release`
> gate failed, the fallback would have needed custom trace naming first —
> record that if this is ever revisited.

---

## 2. Phase R0 — Make every run identifiable *and* self-describing

**Built 2026-07-31.** `experiments/common.py` (`RUN_LABEL`, `health()`,
`check_schema_version()`, `trace()`, `record_app_run()`, `run_context()`,
`last_app_run()`), `run_experiment.py` (`release`, `run_name`,
`results/runs.jsonl`).

**Bucket 1** (test-side). No application change. Cost: zero extra judge
calls.

### R0.1 One run label, three places

Introduce `AML_RUN_LABEL` (default: UTC timestamp; in CI, default to the
dispatching commit SHA). It must land in **all three** of:

1. `Langfuse(release=run_label)` — the **groupable** dimension (dashboards).
2. `run_experiment(run_name=f"{spec_name} - {run_label}")` — human-navigable
   run names, replacing the SDK default `"{name} - {iso_timestamp}"`.
3. `Evaluation(metadata={...})` — the **queryable** detail (scripts,
   drill-down).

### R0.2 `run_context()` in `experiments/common.py`

One function returning a flat `dict[str,str]`, split explicitly into the two
categories S4 depends on:

**Measurement axes** (any change ⇒ runs are NOT comparable):
`judge_model`, `seed_version`, `schema_version`, `reset_before_run`,
`harness_sha`, `scenario_count`.

**Application axes** (change ⇒ comparable, and this is the signal):
`app_model`, `app_prompt_version`, `app_temperature`, `app_build` (see R0.4).

**Cheaper than this section assumed, as built:** `model`, `prompt_version`
and `latency_ms` are returned *inline* by `InvestigateResponse` and
`QueryResponse`, in the responses each task already fetches, and
`RetrieveResponse` returns `retrieval_run_id` and `latency_ms`. So
`record_app_run(response)` costs **no extra call at all**. Only
`model_configuration.temperature` and the per-phase latency split need
`GET /api/agent/trace/{run_id}`, which is fetched lazily and cached per run
— still within the "one extra read per run" budget in §10.

Values must be flat strings; metadata is flattened on export.

`"unavailable"` and `"not_applicable"` are different statements and are not
interchangeable: the retrieval-only path (`POST /api/rag/retrieve`) runs no
LLM, so it has no model or prompt version — that is `"not_applicable"`.
`"unavailable"` means a field that should exist could not be read.

**Fail loudly:** if a fingerprint field is absent, record the literal
`"unavailable"`. Never omit the key and never guess — a comparison that
silently lost its fingerprint is the exact failure S4 exists to prevent.

### R0.3 A local run manifest

Append one row per run to `results/runs.jsonl` (already gitignored):
`run_label`, `experiment_id` (from `ExperimentResult.experiment_id` — the only
precise per-run join key, random per call for local items, and recoverable
nowhere else), `run_name`, both fingerprint blocks, UTC start/end.

This is `regression-testing-plan.md` Phase 0, which that doc says to do
**regardless of Langfuse**, and it is what makes R2 work offline and makes
the whole thing debuggable when the dashboard disagrees with your memory.

### R0.4 Application ask (Bucket 2) — one field

**Delivered 2026-07-31.** `GET /api/health` now returns
`build_version` (string, `"<version>+<short SHA>"`, documented in
`/openapi.json`), and `schema_version` moved to `1.1.0`. The bump was
verified additive — the whole old-vs-new OpenAPI diff is that one field.

**But the deployed image reports the documented `+unknown` fallback**, i.e.
it was built without git metadata, so the value is currently a constant.
`app_build()` therefore records it as `"unavailable"`, and R2 keeps its
"application changes may be invisible" warning. Both clear themselves once
the image is built with its commit. Full verification record:
[`docs/asks/build-version-request.md`](docs/asks/build-version-request.md).

The original statement of the gap, kept because it is why the field exists:

The application exposes **no build identifier**. `/api/health` returns only
`{status, schema_version, eval_tracing}`. `prompt_version` and `model` are
good proxies but do not move when retrieval logic, a tool implementation, or
a threshold changes — so a real regression can appear with *every* fingerprint
field identical, which R2 would then report as "no app change," the precise
misattribution `regression-testing-plan.md` §2 warns about.

**Ask for:** a `build_version` (or `git_sha`) field on `GET /api/health`.
Read-only, one field, no new endpoint. **Stop there** — per `CLAUDE.md`'s
bucket-2 rule, do not design the application-side change from this
repository. Until it exists, `run_context()` records `app_build:
"unavailable"` and **R2 treats that as a warning**, because it means app
changes may be invisible.

The ask is written up, ready to hand over, in
[`docs/asks/build-version-request.md`](docs/asks/build-version-request.md) —
contract, constraints and acceptance criteria, with implementation choices
left to the application team.

**Exit criteria:** two runs with different `AML_RUN_LABEL` produce scores
distinguishable by `traceRelease` in the Langfuse UI, and `results/runs.jsonl`
has two rows. **Met 2026-07-31** — `verify-a`/`verify-b`/`verify-c`, three
manifest rows, and `traceRelease` groupable through the Metrics API (§1).

---

## 3. Phase R1 — Emit the comparables, including honest latency

**Built 2026-07-31.** All 17 `Evaluation(...)` sites stamped with
`run_context(...)`; `common.app_latency` attached as a shared evaluator.

**Bucket 1.** No application change. Cost: unchanged for quality scores;
**zero additional** for latency (read from data already returned).

### R1.1 Quality scores — stamp the existing 17

17 `Evaluation(...)` call sites across
`experiments/{investigate,rag_query,retrieval,conversational}.py` gain
`metadata=run_context()`. Mechanical.

Score names are already stable and distinct (`answer_relevancy`,
`tool_correctness_names`, `tool_correctness_arguments`,
`argument_correctness`, `bias`, `prompt_alignment`, `plan_adherence`,
`hallucination`, `summarization`, `pii_leakage`, `contextual_precision`,
`contextual_recall`, `tool_use`, `multi_turn_mcp_use`, `turn_relevancy`,
`mcp_primitive_accuracy`, `mcp_argument_accuracy`).

**Treat these names as an API** — Langfuse's own best-practices guidance is
explicit that renaming a score silently breaks every dashboard widget, saved
filter and monitor that targets it. Once R3 exists, a rename is a breaking
change, not a tidy-up. Record any rename in `docs/metric-notes.md`.

### R1.2 Latency — as explicit scores, never as Langfuse span duration

**This is the single most important correctness decision in R1.**

Langfuse's `observations` view has a `latency` measure. For our experiment
spans that measures **harness wall-clock — the application call plus the
judge call plus overhead.** Charting it as "application latency" would be
precisely the conflation `performance-latency-plan.md` §5 forbids ("Don't
conflate notebook wall-clock time with application latency").

Instead, emit the application's **own** server-side timings as numeric
scores, so they live in the same store, the same dashboards and the same
monitors as quality scores:

| Score name | Source (`GET /api/agent/trace/{run_id}`) |
|---|---|
| `app_latency_ms` | `latency_ms` (whole run) |
| `app_latency_retrieval_ms` | `steps[]` where `type == "retrieval"` |
| `app_latency_tool_call_ms` | `steps[]` where `type == "tool_call"` |
| `app_latency_synthesis_ms` | `steps[]` where `type == "synthesis"` |

This is `performance-latency-plan.md` Phase 0 — data the suite already pays
for and currently throws away — and it makes S3 a first-class comparable
rather than an inference. Percentiles (`p50`/`p95`) come free from the
Metrics API aggregations, satisfying that doc's Phase 1 without extra code.

**Two deliberate exclusions, as built:**

- A phase score is emitted only when the run has steps of that type. A run
  that called no tools has no tool-call latency; recording `0` would be a
  fabricated measurement, not a fast one.
- `TurnRelevancy` emits **no** latency score. Its task makes three
  application calls and none of them is "the" run the metric is about;
  publishing one turn's latency under the same score name as every other
  metric's whole-run latency, or summing three turns into one number, would
  both be fabricated comparables. The `ToolUse`/`MultiTurnMCPUse`
  conversations do have one — the investigation; the two MCP probes around
  it are the harness's own additions and are not counted.

**Do not** add synthetic load or timing probes here. Load/stress testing
against this live production instance remains out of scope and requires
separate explicit authorization (`CLAUDE.md`, `performance-latency-plan.md`
§4).

**Exit criteria:** one run produces both quality and `app_latency_*` scores,
all carrying `traceRelease` and full metadata. **Met 2026-07-31** — one
`tool_correctness` run emitted `tool_correctness_names`,
`tool_correctness_arguments`, `app_latency_ms`, `app_latency_tool_call_ms`
and `app_latency_synthesis_ms` (no `app_latency_retrieval_ms`: that run had
no timed retrieval step, and an absent score is the honest result).

---

## 4. Phase R2 — The comparability guard (`compare_runs.py`)

**Built 2026-07-31.** `compare_runs.py` at repo root:
`--since`/`--runs`/`--list`/`--force`/`--offline`.

**Bucket 1. This is S4, and it is the phase that must not be cut.**
Read-only; **zero judge calls**; safe to run as often as you like — which is
exactly why it is a separate script from the thing that costs money.

New `compare_runs.py` at repo root, importing nothing from the notebooks.

### R2.1 Refuse before you compare

Given two run labels, first compare their **measurement axes**. If any of
`judge_model`, `seed_version`, `schema_version`, `reset_before_run`,
`harness_sha`, or `scenario_count` differs:

```
REFUSING TO COMPARE baseline-07-30 vs after-planner-change

  judge_model      gpt-5.4-mini   ->  gpt-5.4        CHANGED
  seed_version     scenarios-v1   ->  scenarios-v1   ok
  reset_before_run true           ->  false          CHANGED

These runs are not comparable: the measurement changed, not just the
application. Any score delta below would mix a judge swap and a planner
state difference with whatever the application actually did.

Re-run with the same judge model and AML_RESET_BEFORE_RUN=true, or pass
--force to print the deltas anyway (they will be labelled UNSAFE).
```

Non-zero exit. `--force` is available but every row it prints is labelled
`UNSAFE`, and it never writes a "clean" summary. **The default must be
refusal**, not a warning that scrolls past.

`reset_before_run` deserves emphasis: the planner skips tools already
completed for a case, so a reset run and an un-reset run produce different
tool plans for reasons unrelated to quality. Comparing them is meaningless
for the five tool-planning metrics.

### R2.2 Then compare, with attribution beside every delta

When measurement axes match, pull scores via
`scores_v3.get_many_v3(name=…, from_timestamp=…)` — or by `experiment_id`
from the manifest for precision — group by `run_label`, and print per metric:
baseline, current, delta, and **what changed application-side**:

```
metric                     baseline  current   delta    app change
answer_relevancy             0.92      0.91    -0.01    prompt_version v1->v2
tool_correctness_names       0.88      0.62    -0.26 !! prompt_version v1->v2
app_latency_ms (p95)         7594      9120    +1526 !! prompt_version v1->v2
```

`!!` marks "worth a look," **not** "failed" — see R6. Support N runs, not
just two, so trends are visible.

### R2.3 Also write Phase-0 history

Append every score to `results/scores.jsonl`. Gives a `grep`-able artifact
with no dashboard login, and lets R2 work when Langfuse is unreachable
(`--offline` reads exactly that file).

**Two guards added while building, both cheap and both catching a failure
that otherwise reads as a quality change:**

- A label whose scores disagree with each other on a measurement axis is
  reported as `MIXED:` and refused — that is one label covering two
  different configurations, not one run.
- Score counts are printed per run, and a metric present in one run and
  absent in the other is called out as an incomplete run rather than shown
  as a delta.

**Exit criteria:** comparing two deliberately-mismatched runs refuses with a
specific message and non-zero exit; comparing two matched runs prints a
delta table with app attribution. **Met 2026-07-31** — `verify-a` vs
`verify-b` (same axes) printed the table; `verify-a` vs `verify-c` (judge
model changed) refused, naming `judge_model`, and exited 2. `--force` and
`--offline` both exercised.

**One measurement worth carrying into R6:** `verify-a` and `verify-b` are
two runs of the *same* metric against the *unchanged* application, and their
`app_latency_ms` differs by 19% (9509 → 7684 ms). Quality scores were
identical (1.000, a deterministic metric), but latency is visibly noisy
run to run. Any latency threshold set without measuring that spread first
will fire on ordinary variance — the same argument §R6 makes for judge
scores applies to latency, and the noise band has to cover both.

---

## 5. Phase R3 — Langfuse dashboards (Option 1, the visualisation)

**Bucket 1.** Requires R0's `release` verification gate to have passed.

Build **one dashboard**, not many. Widgets, all on the `scores-numeric` view
unless noted:

| Widget | Purpose | Config |
|---|---|---|
| **Quality by run** | S1 + S2 | Bar; dimension `traceRelease`; breakdown `name`; measure `avg(value)`; filter `name` in the 17 quality scores |
| **Quality trend** | Drift over many runs | Line; time dimension; breakdown `name` |
| **Latency by run** | S3 | Bar; dimension `traceRelease`; measure `p95(value)`; filter `name` in `app_latency_*` |
| **Latency phase split** | Which phase slowed | Bar; breakdown `name` across the three phase scores |
| **Run inventory** | Sanity | Table; dimension `traceRelease`; measure `count` — a run with fewer scores than its predecessor is an incomplete run, not an improvement |

That last widget is a cheap, high-value trap: a partially-failed run
otherwise looks like a quality change.

**Manage dashboards as code where practical** — the public API under
`/api/public/unstable` plus the Langfuse CLI allow version-controlling
widget definitions. Note that namespace is explicitly **unstable and may
change**; if it churns, defining dashboards in the UI and documenting them
here is an acceptable, honest fallback rather than a maintenance burden.

**Caution:** widget-level environment filters override the dashboard-level
selector. **Do not add environment filters at all.** Measured 2026-07-31:
spans report `sdk-experiment` but scores report `default`, so an environment
filter on a scores widget does not narrow the chart — it empties it. See §1.

**PII:** dashboards aggregate scores (numbers, and judge `comment` strings).
No new category of data leaves the machine beyond what
`observability-plan.md` §5 already covers, and masking still applies at the
export boundary. **Judge reasons are free text and can quote the model's
output** — that is not new in R3, but it is worth re-confirming with whoever
owns data protection before widening dashboard access beyond the current
audience.

**Exit criteria:** one dashboard shows ≥2 metrics across ≥2 runs, scores and
latency, grouped by run label.

---

## 6. Phase R4 — Monitoring and alerting

**Bucket 1.** Uses Langfuse **Monitors**, so nothing is built here.

Configure monitors over scores with **warning** and **alert** thresholds:

- Per-metric quality floor (`avg(value)` below threshold on the newest run).
- `p95(app_latency_ms)` ceiling.
- **`count` no-data / low-count monitor** — catches "the suite silently
  stopped running," which is the failure most likely to go unnoticed and is
  invisible to any quality threshold.

Use **no-data handling** deliberately: for an on-demand suite, "no scores in
the last day" is normal, so prefer `NO_DATA` status without alerting, or
alert only after a sustained absence. Getting this wrong is the fastest way
to train everyone to ignore the alerts.

Route to **Slack** and/or **GitHub Actions** (`workflow_dispatch`) — the
latter can trigger this repo's own `langfuse-experiment.yml`, which the
self-hosted runner will pick up now that it runs as a persistent `launchd`
service.

**Watch the plan limit** on number of monitors (2 on Hobby, 20 on Core).
Prefer a few broad monitors over one per score name.

**Cadence and cost discipline:** an alert that triggers a re-run triggers
real judge spend and a real `POST /api/dev/reset` against a live instance.
**Do not wire an alert straight into an automatic full-suite re-run.** Alert
a human; let the human dispatch. This is the same live-application
constraint that governs everything else here.

**Exit criteria:** a deliberately-low score raises a warning in the chosen
channel; a skipped run raises the no-data monitor.

---

## 7. Phase R5 — Drill-down (S5)

**Built 2026-07-31.** `compare_runs.py --explain <metric> <run_label>`, the
persisted `trace_id`/`observation_id` (both verified against the Langfuse
UI's own score export), and the written-up path in `README.md`.

One addition this phase forced, worth recording because it is not in the
sketch below: **score metadata now carries `item_scenario`**, taken from the
dataset item's own metadata, which the SDK already passes to every evaluator
as `metadata`. Without it `--explain` cannot say *which item* a score
belongs to once a metric scores more than one scenario — it would print rows
it could not tell apart. It is deliberately **not** an axis: it identifies
the item, not the run, so two runs that scored different scenarios must not
be refused on account of it.

**Bucket 1.** Documentation plus one small helper; no new data collected.

The path from a moved number to evidence, to be written into `README.md`:

1. **Dashboard** → the metric and run whose score moved.
2. **Langfuse trace** → each score is attached to a `trace_id` *and* the
   task `observation_id`, so the score links to the exact experiment item.
   The judge's own `reason` is on the score as `comment` — usually enough to
   see *why* it scored low.
3. **Item detail** → the item span carries `experiment_name`,
   `experiment_run_name` and the run metadata, with input/expected output
   attached.
4. **Application internals** → score metadata carries the application
   `run_id`. `GET /api/agent/trace/{run_id}` gives the executed step
   timeline with per-step `latency_ms`, `tools_selected`, `skipped_tools`
   and `tool_calls`; `GET /api/eval/export/{run_id}` gives `tools_called`.
   **This is where real debugging happens**, and it is unmasked and local —
   whereas the Langfuse copy is masked by design.

Add `compare_runs.py --explain <metric> <run_label>` to print, per scenario:
score, judge reason, `run_id`, and the app trace URL — collapsing steps 1–4
into one command. Everything it needs is already in `results/scores.jsonl`:
the judge reason as `comment`, the application `run_id` in metadata, and the
Langfuse `trace_id`/`observation_id` alongside them.

**Note the asymmetry deliberately:** Langfuse holds masked data; the
application holds the unmasked detail. That is the correct arrangement
(`observability-plan.md` §5), and it means deep debugging happens against the
application, on this machine — not in a SaaS dashboard.

**Exit criteria:** from a single moved number, reach the failing scenario's
judge reason and the application's step timeline in under a minute. **Met
2026-07-31** — one command prints the scenario, the score, the judge's
reason, both fingerprint blocks, two ready-to-run `curl`s against the
application, and a Langfuse URL focused on that score's own observation.

---

## 8. Phase R6 — Only now, thresholds and gating

**Bucket 1, but sequenced last on purpose.**

`docs/judge-calibration.md` Phase 1 — the noise-band measurement — **still
does not exist**, and without it there is no defensible answer to "is −0.04
a regression?" Current literature on LLM-as-judge reliability reports
meaningful within-judge variance run to run (one commonly cited figure is a
coefficient of variation around 0.066, i.e. a few percent on a 0–1 score,
with wide variation by task and rubric) — small, but the same order as many
real regressions, which is exactly why guessing a threshold fails.

Order of work:

1. **Measure the band.** Run the suite N times (3–5) against an *unchanged*
   application with identical measurement axes. The spread of each metric's
   score across those runs is its noise floor. **This costs N full runs of
   judge spend — budget it explicitly**, and it is the single highest-value
   purchase in this plan.
2. **Set per-metric tolerance** from that band, not one global number. A
   metric judged per-chunk will be noisier than a deterministic one
   (`tool_correctness_names` is deterministic and should have a near-zero
   band; treat any movement there as real).
3. **Prefer paired comparison.** Compare per scenario, then aggregate —
   comparing only run-level means discards the pairing and hides a large
   regression on one scenario offset by noise on another.
4. **Only then** consider a CI gate, and only on metrics whose band is
   narrow enough to justify one. `regression-testing-plan.md` §4 is explicit
   that a gate built before this exists will fire on ordinary variance and
   be disabled within a month.

Until step 2 is done, **`compare_runs.py` reports; a human judges.**

---

## 9. Option 2 — custom dashboard: not now, and the triggers that would change that

Do **not** build a custom dashboard. `regression-testing-plan.md` §4 already
ruled it out as product surface rather than test infrastructure, and R3
delivers S1–S3 with no UI code.

Build one **only** if one of these becomes true, and record which:

- The `release` verification gate fails *and* `traceName` is also unusable,
  leaving no groupable per-run dimension.
- A regulatory or data-protection decision forbids sending scores to
  Langfuse Cloud, and self-hosting (`observability-plan.md` §5) is rejected
  as too much platform to operate.
- The needed view genuinely cannot be expressed in the Metrics API — the
  concrete example being multi-axis correlation (score vs. latency vs. app
  version on one chart), which the API's dimension model does not do well.

If it ever happens, the cheapest honest version is a static HTML report
generated by `compare_runs.py` from `results/scores.jsonl` — not a served
web application. Everything needed is already in that file by R2.

---

## 10. Sequencing, cost, and what each phase does to the live instance

| Phase | Depends on | Judge cost | Effect on live instance |
|---|---|---|---|
| R0 identity + fingerprint | — | none | 1 extra read per run (`/api/agent/trace/{run_id}`) |
| R1 comparables + latency | R0 | none beyond today | reads only |
| **R2 comparability guard** | R0, R1 | **none** | **none** (Langfuse reads only) |
| R3 dashboards | R0 gate, R1 | none | none |
| R4 monitors | R3 | none | none, unless an alert triggers a re-run — keep that human-gated |
| R5 drill-down | R1 | none | reads only |
| R6 noise band | R1 | **N full runs** | N full suites, each with per-metric resets |

**Cadence opinion, unchanged:** run per meaningful checkpoint, not per
commit and not nightly-by-default. A full 14-metric run costs the
application's own LLM calls plus on the order of a hundred judge calls at
`gpt-5.4-mini`. Two *labelled* runs around a known change are worth more
than seven unlabelled nightly ones. R2 costs nothing and can run constantly.

**Every comparison run needs `AML_RESET_BEFORE_RUN=true`** — and that
**drops and recreates every table** on the target instance, once per
investigate-based metric. On a live production instance this is the highest-risk
routine operation in this repo; `CLAUDE.md`'s live-application constraints
apply in full.

---

## 11. Open items to resolve before/while building

1. ~~**Verify `release` → `traceRelease`** end to end (gate on R3).~~
   **Passed 2026-07-31**, verified through the Metrics API rather than the
   UI — see §1. No `traceName` fallback needed.
2. ~~**`PlanAdherenceMetric` appears unblocked.**~~ **Verified passing end to
   end 2026-07-31** (`run_all.py PlanAdherence` with
   `AML_RESET_BEFORE_RUN=true` → `1/1 notebooks passed`, 65s). `README.md`,
   `metric-notes.md`, `observability-plan.md` §3.3 and
   `regression-testing-plan.md` corrected; expected result is now 14/14.

   **Still outstanding, and it matters for R6:** do not let this metric into
   a baseline built from runs that predate the unblock. A metric coming
   online reads as a dramatic "improvement" that is nothing of the kind —
   the comparison is "not scored" vs. "scored," not two measurements of
   quality. Start its score history fresh, and treat its first runs as
   establishing a baseline.

   Also worth noting for R1/R2: `run_all.py` discards notebook output, so
   **this verification produced a pass/fail and no recorded score.** The
   number that would let anyone check this claim later does not exist
   anywhere — which is precisely what `results/scores.jsonl` is for.
3. ~~**Ask for `build_version` on `/api/health`**~~ (R0.4). **Delivered and
   verified 2026-07-31** — field present, documented in `/openapi.json`,
   stable across calls, no measurable cost to the endpoint, and the
   `schema_version` 1.0.0 → 1.1.0 bump confirmed additive field-by-field.

   **Still open, and it is a deployment matter, not an application one:** the
   running image reports the documented `+unknown` fallback, so the field is
   currently a constant and carries no build identity. `app_build()` records
   `"unavailable"` for it and R2 keeps warning that app changes may be
   invisible. Both resolve themselves once the image is built with its
   commit metadata. Record:
   [`docs/asks/build-version-request.md`](docs/asks/build-version-request.md).
4. **Confirm the PII position on judge `comment` strings** with whoever owns
   data protection before widening dashboard access (R3).
5. **Decide who else needs to see this.** If the answer stays "only the
   person who ran it," R3 could be deferred in favour of R2 alone — that is
   the honest trigger `regression-testing-plan.md` §3 sets for adopting a
   dashboard at all.

---

## 12. Honest summary

**Option 1 works.** Langfuse gives dashboards, a metrics API, and threshold
monitors with Slack/webhook/GitHub-Actions routing — S1, S2, S3, S5 and S6
without building a UI. The one real constraint is that **run identity must
travel as `release`, not as score metadata**, because metadata is not a
dashboard dimension.

**The highest-value phase is R2, not R3.** The refusal-to-compare guard is
what makes every number downstream trustworthy, costs nothing to run, and
works with or without Langfuse.

**Latency must come from the application's own `latency_ms`,** never from
Langfuse span duration, which includes judge time and would be a fabricated
comparable.

**Nothing here needs an application change** — except the one small,
worthwhile ask for a build identifier, without which some regressions are
genuinely unattributable. That ask was delivered on 2026-07-31; the field
exists and is documented, but the deployed image reports its `+unknown`
fallback, so in practice attribution is still missing that axis until the
image is built with its commit metadata.

**Do not gate CI on any of this until R6 measures the noise band.** Report
first; judge later.
