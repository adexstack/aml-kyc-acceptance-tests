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

### Status

**§1.1–1.5 done and verified 2026-07-29 — Phase 1 is functionally complete.**

- **§1.1 (provisioning):** done. Langfuse Cloud project
  `aml-kyc-acceptance-tests-synthetic` created under the EU host
  (`cloud.langfuse.com` — confirmed via search that this *is* the EU
  region; US is `us.cloud.langfuse.com`), keys in `.env`.
- **§1.2 (optional dependency):** done. `pyproject.toml` has `langfuse` and
  `dev` optional-dependency groups; `langfuse==4.14.1` pinned (checked
  against the live PyPI JSON API, not guessed). Core `uv sync` stays
  Langfuse-free.
- **§1.3 (masking):** done. `langfuse_mask.py` + `tests/test_langfuse_mask.py`
  — fail-closed, fixtures for every named PII field. **14/14 tests pass.**
  One real bug caught and fixed during §1.5: masking must never be applied
  to the data `task()`/evaluators return — that's what DeepEval actually
  scores, and pre-masking it (as an earlier draft of this code did) would
  silently break any metric that compares real values, like
  `ToolCorrectnessMetric`'s argument check against a masked
  `"***MASKED***"` placeholder. Masking now happens exactly once, at the
  Langfuse SDK's export boundary (`Langfuse(mask=mask)`), confirmed by
  direct SDK testing to receive whole nested objects, not flattened
  scalars — see `langfuse_mask.py`'s docstring and
  `experiments/investigate.py`'s module docstring for the `tool`/`server`
  naming convention this also required (the masker's "name" substring
  match would otherwise redact a tool's own identifier, not just a
  person's name).
- **§1.4 (proof) + §1.5 (all 14 metrics):** done, and now **all 14 have run
  at least once against the live local instance and real Langfuse Cloud**
  (6 by me, 2 by you, 6 by you as a follow-up). `run_experiment.py` is the
  CLI entrypoint (`uv run python run_experiment.py [substring-filter]`,
  `--list`); metric logic lives in
  `experiments/{rag_query,retrieval,investigate,conversational}.py`, one
  from-scratch port per notebook.

  | Metric | Score | Note |
  |---|---|---|
  | `answer_relevancy` | 1.000 | |
  | `hallucination` | 0.800 | |
  | `contextual_precision` | 1.000 | no-LLM ranking metric |
  | `contextual_recall` | (run by you) | |
  | `tool_correctness` | 1.000 / 1.000 | names + arguments; validates expected-tool derivation and live-schema check |
  | `argument_correctness` | 1.000 | |
  | `bias` | (run by you) | |
  | `prompt_alignment` | **raised, no score** — see finding below | |
  | `plan_adherence` | 1.000 | verbose log shows the full 8-step plan matched exactly |
  | `tool_use` | 0.667 | matches the source notebook's own documented authoring-run score |
  | `multi_turn_mcp_use` | 0.793 (args 0.990 / primitive 0.753) | matches the notebook's own authoring-run pattern |
  | `turn_relevancy` | 1.000 | the one genuinely-application-owned multi-turn conversation |
  | `pii_leakage` | 0.000 | **expected** — the adversarial probe; a failing score here is the correct finding (metric-notes.md), not a defect |
  | `summarization` | 0.600 | passes |

  **A real finding, not a bug in the port (2026-07-29):** `prompt_alignment`
  raised its deterministic evidence-labelling guard — the agent's rationale
  for scenario s2 cited tool-call evidence labels (`T1, T2, T3`) that did
  not resolve in the investigation's own `evidence` array. Confirmed this
  isn't a false positive in the check itself: an immediate follow-up
  `POST /investigate` on the same case resolved all six labels
  (`C5, C6, T1-T4`) cleanly, so the checking logic is correct and the
  failure was a genuine instance of the live agent hallucinating an
  evidence citation — likely intermittent, since it depends on LLM
  generation rather than a deterministic code path. **Do not treat a clean
  re-run as this being fixed** — track it, don't dismiss it; this is
  exactly the class of defect `PromptAlignmentMetric`'s deterministic check
  (independent of the judge) exists to catch, and `docs/metric-notes.md`
  currently lists this metric as "Implemented and verified" without this
  caveat, worth a note there once confirmed reproducible.
- **§1.7 (CI):** done. `.github/workflows/langfuse-experiment.yml` —
  `workflow_dispatch` only, no schedule, `runs-on: [self-hosted, macOS,
  ARM64]` (decided 2026-07-29: self-hosted, not a staging deployment).
  Repo pushed to GitHub and runner registered + confirmed listening
  2026-07-29. **Last step before a real dispatch works**: repo secrets/
  variables (`LANGFUSE_*`, `OPENAI_API_KEY`, `AML_API_BASE_URL`, etc.) —
  see §1.7 for the exact list.

**A correction to this plan's own original text, found by inspecting the
installed SDK rather than trusting hosted docs a second time:** the
`get_client(mask=...)` pattern originally sketched here doesn't exist —
`inspect.signature(get_client)` shows it only accepts `public_key`. Mask is
passed via `Langfuse(mask=mask)` directly. Everything else —
`run_experiment` signature, `Evaluation` fields, `LocalExperimentItem`'s
required keys, the `mask` callback contract, and both
`LANGFUSE_BASE_URL`/`LANGFUSE_HOST` env vars — was checked the same way
(`inspect.signature`, reading `langfuse/_client/client.py` and
`langfuse/types.py` directly in the installed package) and matches what's
in the code.

**Next step, your call:** Phase 1 (§1.1–1.7) is functionally complete. Open
items: (1) the `prompt_alignment` finding above — decide whether to raise
it with whoever owns the application, track it as a known intermittent
issue, or investigate further; (2) Phase 1.7's CI workflow needs a GitHub
remote and a network-reachable `AML_API_BASE_URL` before it can actually
run (see §1.7 below); (3) whether to move on to Phase 2 (app-side tracing)
at all — the trigger for that is a concrete question Phase 1 can't answer,
which hasn't come up yet.

### 1.1 Provision Langfuse (Cloud, EU region, synthetic data only)

1. Create an account/project at the **EU-region** Langfuse Cloud endpoint.
   **Confirmed 2026-07-29**: `https://cloud.langfuse.com` *is* the EU host;
   US is the distinct `https://us.cloud.langfuse.com` (also JP/HIPAA hosts
   exist). Don't assume this stays true indefinitely — Langfuse could add
   or rename regions.
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

### 1.7 CI — on-demand only, per your decision — DONE

**Built 2026-07-29**: `.github/workflows/langfuse-experiment.yml` — new CI
surface (no `.github/workflows` existed before this). `workflow_dispatch`
only, no `schedule:` trigger, two inputs:

- `filter` — optional substring on experiment function names, blank runs
  all 14.
- `reset_before_run` — boolean, **defaults to `true`**, maps to
  `AML_RESET_BEFORE_RUN`. Needed for correct first-run planner behaviour on
  the tool-planning metrics; **drops and recreates every table** on
  whatever instance `AML_API_BASE_URL` points to. Exposed as an explicit,
  visible dispatch input rather than hardcoded either way, precisely
  because of that destructiveness — set it `false` if the target instance
  holds anything you don't want wiped.

Steps: checkout (`actions/checkout@v7`) → `astral-sh/setup-uv@v9.0.0` →
`uv sync --extra langfuse` → run `run_experiment.py` with the filter, tee'd
to a file → write that file into `$GITHUB_STEP_SUMMARY`.

**Fixed 2026-07-29, two issues found on first real dispatch:**
- `astral-sh/setup-uv@v9` doesn't resolve — that action only publishes exact
  version tags (`v9.0.0`), not a floating major-version alias like most
  other actions. Pinned to `v9.0.0`.
- `enable-cache: true` was stalling for minutes in the post-job cleanup
  step, uploading `~/.cache/uv` (uv's *global* package cache, ~2.5GB) to
  GitHub's remote cache over a home network. That remote cache exists to
  save GitHub-hosted (ephemeral) runners from re-downloading packages every
  run; it buys nothing on a persistent self-hosted runner, whose disk cache
  already survives between runs. Set to `enable-cache: false`.

**Decision (2026-07-29): self-hosted runner, not a staging deployment.**
Deploying the AML app somewhere network-reachable is out of scope here (a
different repository's job, per CLAUDE.md's bucket-2 rule) — a self-hosted
GitHub Actions runner on your own machine sidesteps that entirely, since it
runs the job locally and can reach `http://localhost:8000` directly. The
workflow's `runs-on` is now `[self-hosted, macOS, ARM64]` (matching what
GitHub auto-assigns a macOS/Apple-Silicon self-hosted runner), not
`ubuntu-latest`.

**You handle GitHub-remote creation and pushing yourself** (per your
choice) — confirm here once the repo exists so I know CI has somewhere to
live. Steps to register the runner, once the repo is up:

1. On GitHub: repo → **Settings → Actions → Runners → New self-hosted
   runner**. Select **macOS** and **ARM64**.
2. GitHub generates the exact download/config commands **and a
   registration token that expires in about an hour** — run the commands
   it shows you at that moment; don't reuse commands from a screenshot or
   an old session, the token won't still be valid.
3. `./config.sh` will prompt for the runner name and labels — accept the
   defaults (`self-hosted`, `macOS`, `ARM64` get applied automatically) so
   they match this workflow's `runs-on`.
4. Run it as a persistent background service rather than in a foreground
   terminal you might close: `./svc.sh install && ./svc.sh start` (GitHub's
   runner package includes this script). Check status with `./svc.sh
   status`.
5. **Security note, from GitHub's own guidance**, already in the workflow
   file's header comment: self-hosted runners are safe on a private repo
   triggered only by `workflow_dispatch` — there's no `pull_request`
   trigger here, so a fork PR can't run arbitrary code on your machine.
   Don't add one without re-reading GitHub's self-hosted-runner security
   docs first, and don't make this repo public with the runner attached
   without that same re-read.

**Confirmed 2026-07-29** — runner registered (`v2.336.0`) and listening
(`√ Connected to GitHub`, `Listening for Jobs`). Keep `./run.sh` (or the
`svc.sh`-installed service) running whenever you want to dispatch this
workflow; it only picks up jobs while connected.

**Known limitation — runner availability, currently: foreground `./run.sh`.**
As of 2026-07-29 the runner is being started by hand with `./run.sh` in a
foreground terminal (`cd
/Users/bola/seyi/AI-LLM/actions-runner-local && ./run.sh`). This works but
has real drawbacks: closing that terminal, sleeping the laptop, or a crash
silently stops the runner, and a `workflow_dispatch` fired while it's down
just sits at "Waiting for a runner to pick up this job..." with no error —
exactly the failure mode seen earlier in this session. This isn't a flaw in
the workflow or a fixable gap in this repo; it's the inherent nature of a
self-hosted runner on a personal machine, and the tradeoff was accepted
deliberately (see the "self-hosted, not a staging deployment" decision
above) — but the *foreground-terminal* way of running it is avoidable.

**TODO, recommended: switch to the `svc.sh`-installed background service**
(step 4 above already recommends this — not yet done):

```bash
cd /Users/bola/seyi/AI-LLM/actions-runner-local
./svc.sh install
./svc.sh start
./svc.sh status        # confirm it's running
```

This installs the runner as a `launchd` service that starts at login and
survives closing the terminal, without changing anything about the
workflow file, the registration, or the security posture (still
`workflow_dispatch`-only on a private repo). Stop the old foreground
`./run.sh` process first (`Ctrl-C`) so two runner processes don't both try
to register the same name. Re-confirm with `√ Connected to GitHub` /
`Listening for Jobs` in `./svc.sh status` output, the same signal used to
confirm registration originally.

**Runner install location (updated 2026-07-29):** the runner's own files
(`config.sh`, `run.sh`, `.credentials`, `_work/`, etc.) live at
`/Users/bola/seyi/AI-LLM/actions-runner-local` — a sibling of this repo, not
inside it. That's a deliberate move: this repo's `git` root has no
visibility outside its own directory, so the runner's credentials and
per-job workspace (which includes a full checkout of this repo under
`_work/`) can never be accidentally staged or committed, no matter what
`.gitignore` says. The repo's own `.gitignore` still carries an
`/actions-runner/` entry from when the runner was expected to live inside
the repo tree — harmless to keep as a safety net, but it's not what's doing
the work now.

**Still needed before a real dispatch will succeed** (repo → Settings →
Secrets and variables → Actions): secrets `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `OPENAI_API_KEY`, relevant `AML_API_KEY*`; variables
`AML_API_BASE_URL=http://localhost:8000` (reachable now that the runner is
local), `LANGFUSE_BASE_URL`, `AML_EXPECTED_SEED_VERSION`,
`DEEPEVAL_JUDGE_MODEL`. Same list as earlier in this section — repeated
here since the runner being live makes this the actual next blocker.

**One thing this workflow still cannot solve for you:**

1. **No per-run deep link.** `ExperimentResult.dataset_run_url` (confirmed
   by reading `langfuse/experiment.py` in the installed package) is only
   populated when scoring against a real Langfuse-hosted Dataset. Every
   experiment in this repo uses local (in-code) dataset items instead — see
   `experiments/*.py` — so this field is always `None` here, and the
   summary step says so rather than fabricate a link. A human has to
   browse to the project and filter `Environment = sdk-experiment`,
   matching the run name printed in the log
   (`aml-acceptance-<metric> - <timestamp>`). Moving to real Langfuse
   Datasets would fix this but is a bigger design change, not something to
   slip in as a CI side effect.

**Secrets/variables you still need to set in the GitHub repo before first
use** (I cannot set these for you): secrets `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `OPENAI_API_KEY`, and whichever `AML_API_KEY*` the
target instance's `AUTH_MODE` requires; variables `AML_API_BASE_URL`,
`LANGFUSE_BASE_URL` (defaults to the confirmed EU host if unset),
`AML_EXPECTED_SEED_VERSION`, `DEEPEVAL_JUDGE_MODEL`.

Revisit scheduling once you've seen a handful of on-demand runs and have a
real cost figure, per `observability-plan.md` §6 step 5 — don't schedule
against a guess. This repo also has no GitHub remote configured yet
(`git remote -v` is empty), so none of this can actually execute in GitHub
Actions until it's pushed to a GitHub repo.

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
