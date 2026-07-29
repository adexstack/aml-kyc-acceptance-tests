# Langfuse observability — implementation plan

**Status: implementation plan, phases 0–1 not yet started.** This turns
[`docs/observability-plan.md`](docs/observability-plan.md) into an ordered,
gated build sequence. Read that document first — this one assumes its
analysis and doesn't re-argue it. Where a phase touches the backend
(`aml-kyc-agentic-platform`, a separate repository this harness has no
access to), this plan states exactly what to change and how to verify it,
but the change itself must be made and confirmed by you in that repository.
**No phase whose "Depends on" column names an earlier phase should start
before that phase is confirmed done**, per the table in §0.

## 0. Decisions locked in for this plan

Settled in conversation on 2026-07-29. If any of these change, the affected
phases below need to be re-scoped before continuing — don't silently keep
building against a stale assumption.

| Decision | Answer | Binds |
|---|---|---|
| Langfuse hosting / PII posture | **Cloud, EU region, synthetic seed data only.** Not a decision to point this integration at real customer-derived data — that requires revisiting §5 of `observability-plan.md` and this plan's Phase 2 note below | Phase 1 |
| CI cadence | **On-demand only** (manually triggered), no schedule yet | Phase 1.7 |
| `PlanAdherenceMetric` unblock (`--extra eval`) | **Applied and confirmed 2026-07-29** — `deepeval_trace` now returns a non-null object (`status: SUCCESS`) from `GET /api/agent/trace/{run_id}` | Phase 0 — **done** |
| Phase 2/3 (application-side tracing, trace linking) | **Full guide included now**, gated behind explicit confirmation before each dependent step, per your instruction | Phase 2, Phase 3 |

Every phase below states, per the CLAUDE.md rubric: which bucket it's in,
which documented endpoint(s) it uses, what it costs per run, what it does to
a live production instance, and what happens when the data it needs isn't
there.

---

## Phase 0 — Unblock `PlanAdherenceMetric` (application-side, backend repo) — DONE

**Confirmed 2026-07-29.** `GET /api/agent/trace/{run_id}` now returns a
non-null `deepeval_trace` (`status: SUCCESS`) after adding `--extra eval` to
the post-`COPY` `uv sync` line in the backend Dockerfile and redeploying.
`PlanAdherenceMetric` is unblocked and included in Phase 1.5's metric
rollout from the start — no need to add it later.

**Bucket 2 — the application's job.** Was unrelated to Langfuse; ran in
parallel with Phase 1 provisioning, not as a hard predecessor to it.

**Root cause** (from `docs/metric-notes.md`): the backend image is built
with `uv sync --frozen --no-dev`, which excludes the `eval` extra. Without
it, `deepeval` is not importable, `configure_tracing()` returns `False`
unconditionally regardless of `EVAL_TRACING`, and `GET
/api/agent/trace/{run_id}` serves `deepeval_trace: null`.

### Steps to apply in `aml-kyc-agentic-platform`

1. Find wherever the backend image is built — `Dockerfile`, a
   `docker-compose.yml` build step, or a CI/CD deploy pipeline — and locate
   the `uv sync --frozen --no-dev` invocation.
2. Change it to `uv sync --frozen --no-dev --extra eval`.
3. Confirm `EVAL_TRACING` is not set to a disabling value in whatever
   environment you rebuild (check `.env`, Helm values, ECS task def, or
   equivalent — wherever this application's runtime config lives).
4. Rebuild the image and redeploy to the instance this harness points at.

*(These file/line references come from `observability-plan.md`, itself read
against the backend on 2026-07-29 — re-verify they still match before
editing, source may have moved since.)*

### Verification — you must confirm before Phase 1.5

Run against the redeployed instance:

```bash
curl -s $AML_API_BASE_URL/api/health | jq .eval_tracing        # expect: true
```

Then trigger one investigation and check its trace:

```bash
RUN_ID=$(curl -s -X POST $AML_API_BASE_URL/api/cases/<id>/investigate | jq -r .run_id)
curl -s $AML_API_BASE_URL/api/agent/trace/$RUN_ID | jq .deepeval_trace
# expect: a non-null object with planned_steps, not null
```

**Cost:** none beyond the investigate call you'd run anyway.
**Production impact:** an image rebuild and redeploy — plan it like any
other deploy, not as a side effect of this test harness.
**If the data still isn't there:** `PlanAdherenceMetric`'s notebook already
fails loudly with the exact reason (`metric-notes.md`) rather than
inferring a plan from the final answer — leave that behavior as is.

**Confirm here before I mark Phase 0 done and include the metric in Phase
1.5's rollout:** paste the two command outputs above, or tell me you've
verified them.

---

## Phase 1 — Test-side Langfuse experiments (this repo, no application change)

**Bucket 1 — test-side, in scope here.** Uses only `GET
/api/eval/scenarios`, `POST /api/cases/{id}/investigate`, `GET
/api/eval/export/{run_id}`, `GET /api/agent/trace/{run_id}` — all already
documented and used elsewhere in this repo. No import from, and no
filesystem dependency on, the application source.

### 1.1 Provision Langfuse (Cloud, EU region, synthetic data only)

1. Create an account/project at the **EU-region** Langfuse Cloud endpoint
   (`https://cloud.langfuse.com` region selector → EU, or the direct EU host
   documented in Langfuse's own docs — confirm the current URL there, don't
   guess it here).
2. Name the project something unambiguous, e.g.
   `aml-kyc-acceptance-tests-synthetic`. The name itself should signal "not
   real data" to anyone who finds it later.
3. Generate a public/secret API key pair scoped to that project only.
4. Add to `.env.example` (blank placeholders, committed) and `.env`
   (real values, already gitignored):

   ```
   LANGFUSE_PUBLIC_KEY=
   LANGFUSE_SECRET_KEY=
   LANGFUSE_HOST=https://cloud.langfuse.com   # or the EU-specific host — confirm current value
   ```

5. **Runtime guard, not just a policy note:** `run_experiment.py` (§1.3)
   must refuse to run if `AML_API_BASE_URL` doesn't resolve to an instance
   whose `GET /api/eval/scenarios` returns the known synthetic
   `seed_version` (`AML_EXPECTED_SEED_VERSION`, already checked elsewhere in
   this repo). This is the same fail-loudly pattern the notebooks already
   use for seed-version drift — reuse it here so a misconfigured
   `AML_API_BASE_URL` can't silently send whatever that instance holds to
   Langfuse Cloud.

### 1.2 Add Langfuse as an optional extra, not a core dependency

Per `observability-plan.md` §3.1, the 14 notebooks must stay Langfuse-free
and installable offline. In `pyproject.toml`, add a new optional-dependency
group (matching the existing `eval`-extra pattern in this repo):

```toml
[project.optional-dependencies]
langfuse = ["langfuse>=<pin-to-current-major>"]
```

`uv sync` (no flags) continues to install zero Langfuse dependencies.
`uv sync --extra langfuse` is required only to run `run_experiment.py`.
Pin the version exactly, the same way `deepeval==4.1.4` is pinned — the SDK
moved to an OpenTelemetry-based API recently and older tutorials online
don't match the current interface.

### 1.3 Write the masking function first, as tested code

Before any run that sends data, per `observability-plan.md` §5 recommendation
3:

- One function, fixtures for every PII-bearing field the seed scenarios can
  produce (customer `name`, `incorporation_or_dob`, `identifiers.dob`,
  beneficiary names, `matches[].listed_name` — cross-check this list against
  current `GET /api/cases/{id}` and `GET /api/customers` response schemas,
  it may have grown).
- Wrapped in `try/except` that **fails closed** — the doc is explicit that
  an exception inside a Langfuse mask function drops the entire export
  batch silently, so a bug in masking must not become a bug in whether data
  gets exported at all. Fail closed means: on exception, redact the whole
  field to a fixed sentinel rather than re-raising into the SDK.
- This is real infrastructure even though Phase 1 only ever sends synthetic
  data — it's the control that must already exist and be trusted the day
  anyone considers pointing a Langfuse-integrated runner at anything else.
  Don't treat it as optional because the current data is synthetic.

Test it as ordinary unit-tested code, not as an integration test against
live Langfuse.

### 1.4 Build `run_experiment.py` — prove the loop on one metric, one scenario

New file at repo root, alongside `run_all.py`, importing nothing from the
notebooks (per `observability-plan.md` §3.1). Base it on the sketch in
`observability-plan.md` §4 (Phase 1), adapted to:

- Apply the masking function from §1.3 to every span/generation before
  export.
- Include `run_id` in dataset-item metadata (this is Phase 3 Option A from
  `observability-plan.md` §4.3 — no application change needed, `run_id` is
  already returned by `POST /api/cases/{id}/investigate` and is the join key
  back to `GET /api/eval/export/{run_id}` and `GET
  /api/agent/trace/{run_id}`). Do this now, not as a separate later phase —
  it costs nothing extra once `run_id` is already in hand.
- Start with exactly one metric (`AnswerRelevancyMetric`, the simplest,
  already implemented and verified per `metric-notes.md`) and one scenario.
- `max_concurrency=1` for this first proof run — the doc's warning about the
  planner skipping already-completed tools for a case applies at any
  concurrency above 1, and there's no reason to risk noisy scores on the
  very first proof.

**Verification gate — confirm before I extend to more metrics:**
Run it, then confirm in the Langfuse UI: a dataset run appears, one score is
attached with the judge's reason string, and a second execution of the same
script produces a second, comparable run. Tell me you've seen this (a
screenshot or the run URL is enough) before Phase 1.5.

### 1.5 Extend to all metrics that pass today

Once 1.4 is confirmed: add an evaluator function per remaining metric —
all 14 now that Phase 0 is confirmed, including `PlanAdherenceMetric`, whose
golden-derivation logic (declared plan vs. executed trace) now has real data
to compare via `GET /api/agent/trace/{run_id}`. Reuse the golden-derivation
logic each notebook
already implements — this is the accepted duplication cost named in
`observability-plan.md` §3.1, not something to abstract into a shared
package the notebooks would then depend on.

Keep `max_concurrency` low (2 per the doc's sketch) and prefer running
against a freshly reset instance (`AML_RESET_BEFORE_RUN` semantics) so scores
aren't noisy from the planner skipping already-completed tools.

### 1.6 Cost

Every run costs twice: the application's own LLM calls (retrieval + MCP
tool calls + synthesis per scenario) plus the judge call per metric per
scenario (some metrics, `ContextualPrecision`/`Recall`, judge per retrieved
chunk). With 8 scenarios × 14 metrics, budget for on the order of a hundred
judge calls per full run at `gpt-5.4-mini` — cheap individually, non-trivial
in aggregate. This is the reason cadence (§1.7) matters.

### 1.7 CI — on-demand only, per your decision

No `.github/workflows` directory exists in this repo yet — this is new CI
surface, not an addition to something already there. Add a manually
triggered workflow (`workflow_dispatch`, no `schedule:` trigger) that:

1. Checks out the repo.
2. `uv sync --extra langfuse`.
3. Runs `run_experiment.py` with `LANGFUSE_*` and `AML_API_BASE_URL` from
   repository secrets/environment.
4. Surfaces the Langfuse run URL in the job output or summary so a human
   can click through.

Revisit scheduling once you've seen a handful of on-demand runs and have a
real cost figure, per `observability-plan.md` §6 step 5 — don't schedule
against a guess.

**Production impact of Phase 1 as a whole:** every run performs real writes
against the target instance (`investigate` calls), same as `run_all.py`
already does today — no new category of production impact, just a second
caller of the same endpoints. Treat concurrent/cadence caution the same way
you already do for `run_all.py`.

---

## Phase 2 — Application-side tracing (application change, trigger-gated)

**Bucket 2 — valuable, but the application's job.** Do not start this
until a concrete question comes up that Phase 1 cannot answer — e.g. "this
metric regressed and I can't tell which tool call caused it." Don't build
it speculatively.

**Before this phase can point at anything but a synthetic/staging
environment, the hosting decision in §0 must be revisited.** Phase 1's
"Cloud EU, synthetic only" answer does not extend to Phase 2 by default —
application-side tracing captures real request/response content, and if
this backend ever runs against real customer data, tracing that data to
Langfuse Cloud is a different risk decision than the one made for Phase 1.
Don't assume the Phase 1 answer carries forward; ask again when this phase
becomes live.

### Steps to apply in `aml-kyc-agentic-platform`

The backend already has the seam Langfuse needs, per `observability-plan.md`
§4 Phase 2: its own `observe` wrapper at `backend/app/observability.py:70`,
deliberately indirecting DeepEval's decorator rather than applying it
directly, used at:

| Decorator | File (re-verify against current source — this reference is from `observability-plan.md`, read 2026-07-29) |
|---|---|
| `@observe(type="agent", name="investigation")` | `backend/app/services/agent/investigation.py:136` |
| `@observe(type="llm", name="synthesis")` | `backend/app/services/agent/investigation.py:283` |
| `@observe(type="tool")` | `backend/app/services/agent/investigation.py:486` |
| `@observe(type="agent", name="rag_query")` | `backend/app/services/rag/qa.py:104` |
| `@observe(type="llm", name="generation")` | `backend/app/services/rag/qa.py:91` |
| `@observe(type="retriever")` | `backend/app/services/rag/retrieval.py:39` |

1. Add the Langfuse Python SDK as a real backend dependency (this is
   production dependency surface, not test-only — treat it with the same
   care as any other new prod dependency: pin the version, review its
   transitive deps).
2. Extend the existing `observe` wrapper in
   `backend/app/observability.py` (do **not** import Langfuse's own
   `@observe` directly into `investigation.py`, `qa.py`, or `retrieval.py`)
   so one wrapper can emit to DeepEval, Langfuse, both, or neither, keyed
   off configuration. This preserves the existing `EVAL_TRACING` switch and
   keeps the choice in one file.
3. Add a new env var, e.g. `LANGFUSE_TRACING_ENABLED`, independent of
   `EVAL_TRACING` — the two serve different consumers (DeepEval's own
   plan-adherence trace vs. an operational observability trace) and
   shouldn't be conflated into one flag.
4. Apply the masking approach from Phase 1.3 here too, or a superset of it —
   this path now carries real request/response content, not just
   scenario metadata.
5. Deploy to a **non-production** environment first.

### Verification — confirm before Phase 3 (if you pursue it)

- `GET /api/health` (or wherever this backend surfaces flags) reports the
  new tracing flag active.
- Trigger a real `investigate` call in the non-prod environment and confirm
  spans appear in the Langfuse UI with the expected span tree (agent →
  llm/tool children), and that masked fields are actually redacted, not just
  present-but-not-yet-checked.

**Cost:** ongoing per-request overhead (span creation, network export) on
the agent hot path, plus whatever Langfuse ingestion costs at this project's
volume. **Production impact:** a real backend code change touching the
agent hot path — needs its own tests and a normal deploy process, not a side
effect of a test-harness change. **If the SDK or export fails:** per
`observability-plan.md` §5, a masking-function exception drops the whole
export batch — make sure that failure mode degrades to "no trace for this
request" and never to "request fails," i.e. tracing must not be able to take
down the agent path it's observing.

**Confirm here before Phase 3:** tell me this is deployed and you've seen
traced spans in Langfuse for a real (non-prod) investigation.

---

## Phase 3 — Trace linking beyond `run_id` correlation (optional, likely unnecessary)

**Bucket 2 if pursued — application change.** Phase 1.4 already gives you
Option A (correlate by `run_id`, no application change) for free. This phase
is Option B from `observability-plan.md` §4.3: propagating W3C trace context
so a Langfuse trace and the application's internal trace are the *same*
trace rather than two records joined by a shared id.

**Do not start this speculatively.** Per the source doc: revisit only if
you find yourself repeatedly unable to answer a real question without a
unified flame graph — `run_id` correlation answers "which application run
produced this score," which is most of the practical need.

If it does become necessary: the backend would need to accept an inbound
`traceparent` header and continue that trace rather than starting its own.
Note the tradeoff named in the source doc before doing this: it means a test
client can influence server-side trace identity, which is worth thinking
about explicitly for any internet-facing deployment before implementing it.

---

## What this plan deliberately does not do

- **Does not touch the 14 notebooks.** They stay Langfuse-free and portable,
  per `observability-plan.md` §3.1 — `run_experiment.py` is a sibling to
  `run_all.py`, not a replacement or a shared import.
- **Does not adopt Langfuse prompt management** for application prompts —
  `observability-plan.md` §3.2's governance argument stands; out of scope
  here entirely.
- **Does not treat Langfuse as a shortcut for `PlanAdherenceMetric`.**
  Phase 0 is the actual fix; Langfuse traces are a different shape and
  wouldn't satisfy that metric even if adopted first.
- **Does not schedule anything in CI yet**, per your cadence decision —
  Phase 1.7 ships on-demand only; revisit with real cost data in hand.

## Open items to revisit, not blocking Phase 1

- Exact current EU-region Langfuse Cloud hostname/URL — confirm against
  Langfuse's own docs at provisioning time, don't hardcode a URL from this
  plan without checking it's still current.
- Exact Langfuse Python SDK version to pin in `pyproject.toml` — check
  what's current when Phase 1.2 is implemented, the interface has moved
  materially per `observability-plan.md`'s opening caveat.
- The full current list of PII-bearing fields for the masking function
  (§1.3) — cross-check against live `/openapi.json` schemas at
  implementation time, the list above is a starting point, not guaranteed
  exhaustive.
