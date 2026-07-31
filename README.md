# AML/KYC acceptance tests (DeepEval)

Black-box acceptance tests for the AML/KYC investigation API. Each notebook
exercises the running application over its documented HTTP API and grades one
DeepEval metric against the response.

These tests assume **no access to the application's source code**. They import
nothing from the application, read no database directly, and depend on no file
from the repository they happen to live in. Copy this directory anywhere and it
still runs.

## Prerequisites

- Python 3.12 or newer
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- A reachable instance of the AML/KYC API
- An OpenAI API key for the judge model

## Installation

```bash
cd acceptance-tests
uv sync
```

That creates `.venv/` from `pyproject.toml` and `uv.lock`. Nothing from the
parent repository is used or needed.

## `.env` configuration

```bash
cp .env.example .env
```

Then edit `.env`. Notebooks look for `.env` next to the notebook first, then one
directory up, so a single `.env` at the root of this directory covers all of
them.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | **Yes** | none | Judge model credentials. Required by every notebook, including `ToolCorrectnessMetric` - see the note below |
| `AML_API_BASE_URL` | No | `http://localhost:8000` | Base URL of the application API |
| `AML_API_TIMEOUT_S` | No | `180` | Per-request timeout. An investigation runs several tools plus an LLM synthesis, so it is deliberately generous |
| `DEEPEVAL_JUDGE_MODEL` | No | `gpt-5.4-mini` | Judge model id. See "Judge model" below |
| `AML_API_KEY` | Only if the app runs with `AUTH_MODE=api_key` | empty | Single key used for all roles |
| `AML_API_KEY_ANALYST` | No | falls back to `AML_API_KEY` | Key for analyst-scoped calls |
| `AML_API_KEY_EVAL_READER` | No | falls back to `AML_API_KEY` | Key for `/api/agent/trace/*`, `/api/eval/*`, `/api/mcp/{servers,tools}` |
| `AML_API_KEY_TEST_OPERATOR` | No | falls back to `AML_API_KEY` | Key for `POST /api/dev/reset` and `POST /api/mcp/invoke` |
| `AML_EXPECTED_SEED_VERSION` | No | `scenarios-v1` | Guards against goldens being graded against different seed data |
| `AML_RESET_BEFORE_RUN` | No | `false` | When `true`, calls `POST /api/dev/reset` before the run. See "Repeat runs" |
| `AML_RUN_LABEL` | No | UTC timestamp of the run, e.g. `20260731T155633Z` | `run_experiment.py` only. Names the run everywhere it is stored. Pass it per run on the command line, not in `.env` — see "Comparing runs" |

Leaving the API keys blank is correct when the application runs with
`AUTH_MODE=off`, which is its default.

**`OPENAI_API_KEY` is required even for the one metric that uses no judge.**
`ToolCorrectnessMetric` scores deterministically, but in DeepEval 4.1.4 its
constructor still builds a `GPTModel` and raises `DeepEvalError` without a key.
The key is never used to compute that score.

## Pointing the tests at a running application

Set `AML_API_BASE_URL` to wherever the API is listening, then confirm
reachability before running anything:

```bash
curl -s "$AML_API_BASE_URL/api/health"
# {"status":"ok","schema_version":"1.1.0","eval_tracing":true,"build_version":"0.1.0+unknown"}
```

Every notebook performs this check itself in its configuration cell and stops
with a specific message if the application is unreachable, the contract
version is incompatible, or the seed version does not match the one the
goldens were authored against.

Two fields on that payload matter to more than reachability:

- **`schema_version`** is the API contract version. These tests were written
  against `1.0.0` and accept any `1.x`: a MINOR bump is additive by the
  application's own contract policy. A MAJOR change raises, because fields
  may have been removed or retyped.
- **`build_version`** identifies the deployed code, and is what makes a score
  change attributable to an application change rather than judge noise. The
  application documents `"<version>+unknown"` as its fallback when the image
  was built without git metadata — a constant, which is why this harness
  records it as `unavailable` rather than as a build identity. If you see
  `+unknown`, the build is not passing its commit into the image; see
  [docs/asks/build-version-request.md](docs/asks/build-version-request.md).

## Running

### One notebook

```bash
uv run python run_all.py ToolCorrectness
```

Or open it interactively and use **Restart Kernel and Run All** - every notebook
is written to run top to bottom from a fresh kernel with no hidden state:

```bash
uv run jupyter lab notebooks/ToolCorrectnessMetric.ipynb
```

### All notebooks, non-interactively

```bash
uv run python run_all.py
```

Each notebook runs in its own fresh kernel. The exit status is the number of
failures, so CI can gate on it directly. Raise `--timeout` (seconds per cell) on
a slow instance.

### From a completely clean environment

```bash
git clone <repo> && cd <repo>/acceptance-tests   # or just copy this directory
uv sync
cp .env.example .env && $EDITOR .env             # set OPENAI_API_KEY, AML_API_BASE_URL
uv run python run_all.py
```

## Expected output

`run_all.py` prints one line per notebook and a summary:

```
--> AnswerRelevancyMetric.ipynb
    PASS  (34s)
...
14/14 notebooks passed
```

**14 of 14 is the expected result against an instance reporting
`eval_tracing: true`, run with `AML_RESET_BEFORE_RUN=true`.**
`PlanAdherenceMetric` was previously blocked; it was unblocked and verified
passing on 2026-07-31. (The other 13 were last verified before that date —
see `docs/metric-notes.md` for exactly what has been observed and when.)

Two things reliably turn that into a lower number, neither of which is a
defect:

Without a reset, the metrics that depend on first-run planner behaviour fail
loudly rather than scoring, because the planner skips tools already
completed for a case:

```
    FAIL  PlanAdherenceMetric.ipynb: RuntimeError: The trace reports no tools_selected,
    so there is no declared plan to judge adherence against. On a repeat run for the
    same case the planner skips tools it already completed - set
    AML_RESET_BEFORE_RUN=true and re-run.
```

And against an instance *without* agent tracing, `PlanAdherenceMetric` still
fails by design, with an explicit message rather than a score:

```
13/14 notebooks passed
  FAILED  PlanAdherenceMetric.ipynb: RuntimeError: The application reports eval_tracing: false, ...
```

Inside each notebook, expect the request, the raw response, the mapped
DeepEval fields, the derived golden, and then the score with the judge's reason.

A metric scoring below its threshold is a **reported result, not a crash**: the
notebook prints the score, the pass/fail verdict and the reason. Notebooks raise
only when the test itself cannot be conducted honestly - unreachable API, schema
or seed mismatch, missing trace data, or a golden that no longer matches the
served corpus.

## Blocked and limited metrics

| Metric | State |
|---|---|
| `PlanAdherenceMetric` | **Unblocked 2026-07-31.** Was blocked while the backend image omitted the `eval` extra, leaving `deepeval_trace: null`. The application now reports `eval_tracing: true` and serves a declared plan. Still raises, rather than scoring, against any instance with tracing off |
| `MultiTurnMCPUseMetric` | **Limited by a dependency pin.** `mcp` must stay `<1.28`; 1.28 renamed `structuredContent`, which DeepEval 4.1.4 still reads |
| `SummarizationMetric` | **Limited.** No dedicated summarisation endpoint exists; a RAG answer over a known policy document stands in |
| `BiasMetric` | **Limited.** One case cannot demonstrate disparate treatment; it checks the language of a single output |
| `PIILeakageMetric` | **Limited.** Sees only the response body, not prompts or logs |

Full reasoning, required inputs and the application changes needed are in
[`docs/metric-notes.md`](docs/metric-notes.md).

## Design docs for extending this framework

Working on Claude Code in this repo, or planning new test capabilities?
Start with [`CLAUDE.md`](CLAUDE.md) — it indexes the rest and sets the
framework every proposal here gets judged against: is it test-side and
valuable, is it actually the application's job, or does it add no real
value at all. The individual design docs live in [`docs/`](docs/):

- [`docs/observability-plan.md`](docs/observability-plan.md) — score history
  and dashboards (Langfuse), application-side tracing, the PII and
  prompt-governance stance
- [`docs/judge-calibration.md`](docs/judge-calibration.md) — LLM-judge
  variance, model drift, threshold provenance
- [`docs/regression-testing-plan.md`](docs/regression-testing-plan.md) — run
  history, what counts as a regression versus judge noise or environment
  drift
- [`docs/performance-latency-plan.md`](docs/performance-latency-plan.md) —
  what black-box latency testing can honestly claim, and why load/stress
  testing against this live application is explicitly out of scope here
- [`docs/monitoring-plan.md`](docs/monitoring-plan.md) — lightweight
  reachability/drift canaries, distinct from eval-quality observability
- [`docs/governance-plan.md`](docs/governance-plan.md) — verifying the
  application's existing audit trail (decisions, EOD report, role
  boundaries) from outside

## Langfuse experiment runner (optional, separate from the core suite)

`run_experiment.py` (repo root) re-runs the same metrics through Langfuse to
build score history instead of DeepEval's pass/fail notebook output. It's a
separate entry point, not a replacement for `run_all.py` — the core suite
above stays Langfuse-free and installable offline by design; see
[`docs/observability-plan.md`](docs/observability-plan.md) §3.1 for why.

```bash
uv sync --extra langfuse                           # once per checkout - see below
uv run python run_experiment.py                    # all 14 metrics
uv run python run_experiment.py tool_correctness    # substring filter on experiment names
uv run python run_experiment.py --list              # what you can filter on
```

### Which argument selects which metric

**The two runners take different names, and this trips people up.**
`run_all.py` filters on the *notebook filename* (`SummarizationMetric`);
`run_experiment.py` filters on the *experiment function name*
(`summarization`). Both are case-insensitive substring matches, so
`run_experiment.py SummarizationMetric` matches nothing — it fails loudly and
prints the valid names rather than silently running zero metrics.

**`uv run python run_experiment.py --list` is the source of truth.** The
table below is checked against it by `tests/test_run_comparison.py`, so it
cannot drift silently.

| Metric | `run_all.py` (notebooks) | `run_experiment.py` (Langfuse) | Score name(s) emitted |
|---|---|---|---|
| AnswerRelevancy | `AnswerRelevancy` | `answer_relevancy` | `answer_relevancy` |
| PIILeakage | `PIILeakage` | `pii_leakage` | `pii_leakage` |
| Summarization | `Summarization` | `summarization` | `summarization` |
| Hallucination | `Hallucination` | `hallucination` | `hallucination` |
| ContextualPrecision | `ContextualPrecision` | `contextual_precision` | `contextual_precision` |
| ContextualRecall | `ContextualRecall` | `contextual_recall` | `contextual_recall` |
| ToolCorrectness | `ToolCorrectness` | `tool_correctness` | `tool_correctness_names`, `tool_correctness_arguments` |
| ArgumentCorrectness | `ArgumentCorrectness` | `argument_correctness` | `argument_correctness` |
| Bias | `Bias` | `bias` | `bias` |
| PromptAlignment | `PromptAlignment` | `prompt_alignment` | `prompt_alignment` |
| PlanAdherence | `PlanAdherence` | `plan_adherence` | `plan_adherence` |
| ToolUse | `ToolUse` | `tool_use` | `tool_use` |
| MultiTurnMCPUse | `MultiTurnMCPUse` | `multi_turn_mcp_use` | `multi_turn_mcp_use`, `mcp_primitive_accuracy`, `mcp_argument_accuracy` |
| TurnRelevancy | `TurnRelevancy` | `turn_relevancy` | `turn_relevancy` |

So the Summarization equivalent of the ToolCorrectness example is:

```bash
uv run python run_experiment.py summarization
```

Every experiment except `turn_relevancy` also emits `app_latency_ms`, and the
investigate- and query-based ones add `app_latency_retrieval_ms`,
`app_latency_tool_call_ms` and `app_latency_synthesis_ms` where the run
actually has steps of that type. `turn_relevancy` emits no latency score on
purpose — it makes three application calls and none of them is "the" run the
metric is about.

**Score names are an API.** Renaming one silently breaks every Langfuse
dashboard widget, saved filter and monitor pointing at it, and orphans the
history in `results/scores.jsonl`. Treat a rename as a breaking change and
record it in `docs/metric-notes.md`.

### When you need `uv sync --extra langfuse`

**Once per checkout, not per run.** Verified against uv 0.11.24:

- `uv run python run_experiment.py` does **not** strip the extra — once
  installed, it stays installed, so the sync line is not part of the
  run-a-comparison loop.
- Re-run it after `git pull` changes `pyproject.toml` or `uv.lock`.
- **A later plain `uv sync` removes it again** — `uv sync` makes the
  environment match exactly what you asked for, and without `--extra
  langfuse` that means uninstalling `langfuse` and 6 OpenTelemetry/protobuf
  packages. If `run_experiment.py` suddenly fails with
  `ModuleNotFoundError: No module named 'langfuse'`, this is why; re-run the
  sync.

If you would rather not think about it, `uv run --extra langfuse python
run_experiment.py …` installs-if-missing on every invocation and is always
safe. The offline suite (`run_all.py`, the 14 notebooks) never needs any of
this — it must stay Langfuse-free, per `docs/observability-plan.md` §3.1.

Needs `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env`, in addition
to the variables above. It applies the same PII-masking function to
everything it sends and refuses to run unless `AML_API_BASE_URL` resolves to
an instance serving the known synthetic seed data — see
`docs/observability-plan.md` §5 for the PII stance this repo holds itself to.

### Comparing runs

Label a run and its scores become comparable later; leave it unlabelled and
they get a UTC timestamp, which is only useful if you remember which
timestamp was which.

`AML_RUN_LABEL` is the only thing that differs between an unlabelled and a
labelled run. It travels to three places, which is why the same string shows
up in the UI, in a dashboard group-by, and in a script:

| Where it lands | As | Why that place |
|---|---|---|
| `Langfuse(release=…)` | the `traceRelease` dimension | the only per-run key you can **group a dashboard by** — score metadata cannot be grouped on |
| `run_experiment(run_name=…)` | `aml-acceptance-summarization - <label>` | what you read in the Langfuse runs list |
| `Evaluation(metadata=…)` | `run_label` on every score | what `compare_runs.py` groups and filters by |

So a run started **without** `AML_RUN_LABEL` appears as
`aml-acceptance-summarization - 20260731T155633Z` (the UTC time the process
started), and one started **with** `AML_RUN_LABEL=verify-summarization`
appears as `aml-acceptance-summarization - verify-summarization`. Same
command, same metric, same scores — only the label differs. Set it per run
on the command line; putting it in `.env` would stamp every future run with
one label and silently merge unrelated runs.

```bash
AML_RUN_LABEL=before-planner-fix AML_RESET_BEFORE_RUN=true \
  uv run python run_experiment.py
# ... change the application ...
AML_RUN_LABEL=after-planner-fix AML_RESET_BEFORE_RUN=true \
  uv run python run_experiment.py

uv run python compare_runs.py --list                 # what labels exist
uv run python compare_runs.py --runs before-planner-fix after-planner-fix
uv run python compare_runs.py --offline              # from results/scores.jsonl
```

**Give Langfuse a moment before comparing.** Scores become queryable a few
seconds to a couple of minutes after a run finishes, so a comparison started
immediately can read a partial set — which `compare_runs.py` will correctly
flag as "the runs do not carry the same number of scores." Re-run it; if the
counts still differ once ingestion has settled, that is a genuinely
incomplete run rather than a quality change.

`compare_runs.py` is read-only and costs **no judge calls** — it queries
stored scores and prints a table, so run it as often as you like. It
**refuses, with a non-zero exit, when the two runs are not comparable**: a
different judge model, seed version, contract version, reset setting,
harness commit or scenario count means the instrument moved rather than the
thing being measured. `--force` overrides that and labels every row `UNSAFE`.

`AML_RESET_BEFORE_RUN=true` is not optional for a comparison run — the
planner skips tools already completed for a case, so a reset run and an
un-reset one produce different tool plans for reasons unrelated to quality.
Note that reset **drops and recreates every table** on the target instance,
once per investigate-based metric.

Each run also appends to two gitignored files: `results/runs.jsonl` (one row
per experiment, with the `experiment_id` join key and the run's fingerprint)
and `results/scores.jsonl` (every score `compare_runs.py` has read, with its
judge reason, its fingerprint, and the Langfuse `trace_id`/`observation_id`
to drill down with). Both are plain JSONL you can `grep` without a dashboard
login.

`!!` in the output means "worth a look", not "failed": the judge-noise band
(`docs/judge-calibration.md` Phase 1) does not exist yet, so nothing here
gates anything. See `plan.md` §R6.

### From a moved number to the evidence

A delta on its own tells you nothing about *why*. `--explain` collapses the
whole path into one command:

```bash
uv run python compare_runs.py --explain tool_correctness_names after-planner-fix
```

It prints, for each item that metric scored in that run:

- the score, and the **judge's own reason** — usually enough on its own to
  see why it scored the way it did
- the **application fingerprint** (`run_id`, model, prompt version,
  temperature, build) and the **measurement axes** (judge, seed, contract,
  reset, harness commit) — so you can tell "the app changed" from "the
  instrument changed" without leaving the output
- two ready-to-run `curl`s against the application, and a deep link to the
  Langfuse trace focused on that score's own observation

If you prefer to walk it yourself, the same four steps are:

1. **`compare_runs.py`** (or a Langfuse chart) — which metric and which run
   moved.
2. **The judge's reason** — stored as the score's `comment`, printed by
   `--explain`, and shown on the score in Langfuse.
3. **The Langfuse item** — the item span carries the experiment name, run
   name, run metadata, and the input/expected output. Reach it via the URL
   `--explain` prints, or from `trace_id`/`observation_id` in
   `results/scores.jsonl`.
4. **The application's own internals** — `GET /api/agent/trace/{run_id}`
   gives the step timeline with per-step `latency_ms`, `tools_selected`,
   `skipped_tools` and `tool_calls`; `GET /api/eval/export/{run_id}` gives
   `input`, `actual_output` and `tools_called`. The score metadata carries
   the `app_run_id` you need for both.

**Note the asymmetry, it matters:** Langfuse holds a *masked* copy
(`docs/observability-plan.md` §5), while the application holds the unmasked
detail and is local. Step 4 is where real debugging happens; steps 1–3 tell
you where to point it.

`--explain` works with `--offline` too — it prints the trace and observation
ids instead of a clickable URL, since building one needs the project id from
the API.

### CI: on-demand dispatch via a self-hosted runner

[`.github/workflows/langfuse-experiment.yml`](.github/workflows/langfuse-experiment.yml)
runs `run_experiment.py` on `workflow_dispatch` only — no `schedule:`
trigger, since every run costs real judge and application LLM calls. It
requires a **self-hosted** runner, not `ubuntu-latest`, because it needs to
reach `AML_API_BASE_URL` on `localhost`. Runner registration, required repo
secrets/variables, and the current confirmed status are tracked in
`plan.md` §1.7, not here — that's machine-specific operational state, not
something a portable copy of this repo needs.

**The runner must be actively running for a dispatch to work at all** — a
self-hosted runner is a process that polls GitHub, not an always-on service
GitHub provides. If nothing is listening, `workflow_dispatch` just sits at
"Waiting for a runner to pick up this job..." indefinitely, with no error.
It runs as a `launchd` background service (`./svc.sh`, not foreground
`./run.sh`) so it survives closing the terminal and restarts at login — see
`plan.md` §1.7 for registration details and current status.

#### Controlling the runner service

`svc.sh` lives in the runner's own installation directory, **not** in this
repository. Every command below must be run from that directory — the script
resolves the runner root from the current working directory (`pwd`), so
running it from anywhere else either fails outright or, on `install`, bakes
the wrong path into the launchd job:

```bash
cd ~/seyi/AI-LLM/actions-runner-local   # wherever your runner was unpacked
./svc.sh status
```

| Command | What it does |
|---|---|
| `./svc.sh status` | Prints the plist path and `Started` / `Stopped`, or `not installed` if the service was never installed. Use this first — it answers "is a dispatch going to be picked up?" |
| `./svc.sh start` | Loads the launchd job and starts polling GitHub. Run this after a `stop`. |
| `./svc.sh stop` | Unloads the job. The runner stops polling; dispatches queue at "Waiting for a runner…" until it's started again. |
| `./svc.sh install` | **One-time only.** Creates `~/Library/LaunchAgents/actions.runner.*.plist`, the log directory, and `runsvc.sh`. |
| `./svc.sh uninstall` | Stops the service and deletes the plist. Only needed when retiring or relocating the runner. |
| `./svc.sh` (no argument) | Prints usage. There is no `help` subcommand — any unrecognised argument prints the same usage text. |

**Do you have to run `install` every time? No.** `install` is a one-time
setup step. It refuses to run again if the plist already exists, failing with
`error: exists /Users/<you>/Library/LaunchAgents/actions.runner.*.plist`, and
that error is expected, not a problem to fix. Once installed, the service
restarts automatically at login, so day-to-day you only ever need `status`,
`stop`, and `start`. Re-run `install` only after an `uninstall`, or if you
move the runner directory — the installed plist hard-codes the path it was
installed from, so a moved runner needs `uninstall` then `install` from the
new location.

## How trace-dependent metrics obtain internal data

Metrics needing more than the final answer - invoked tools, arguments, MCP
calls, retrieval context - read it from documented endpoints only:

| Data needed | Source |
|---|---|
| Tools called and their arguments | `GET /api/eval/export/{run_id}` (`tools_called[]`) |
| Executed step timeline | `GET /api/agent/trace/{run_id}` (`steps[]`) |
| Declared plan | `GET /api/agent/trace/{run_id}` - `tools_selected[]` and `skipped_tools[]` (each with the agent's `reason`) plus `retrieval_runs[].query`, gated on a non-null `deepeval_trace` |
| MCP servers and tool schemas | `GET /api/mcp/servers`, `GET /api/mcp/tools` |
| Individual tool results | `POST /api/mcp/invoke` |
| Retrieval context | `POST /api/rag/retrieve` (`chunks[]`) |

The application annotates internals with DeepEval's `@observe`, but **that alone
does not expose anything to this harness**. `@observe` fills an in-process trace
manager; a separate process sees only what an endpoint serialises. Where the
data is not served, the affected notebook stops with an explicit message instead
of inferring internals from the final answer.

## Portability constraints

- Notebooks import only third-party packages declared in `pyproject.toml` and
  read configuration from environment variables.
- No notebook imports the application, another notebook, or a local helper
  module. Each is self-contained, which is why the API helper and configuration
  block are repeated in all fourteen.
- `run_all.py` is a convenience runner only; notebooks never import it.
- Case ids are never hard-coded. They are resolved at run time through
  `GET /api/eval/scenarios`, because seed ids are assigned by insert order and
  would otherwise rebind to different cases.
- `uv.lock` is committed so a detached copy resolves identical versions.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `MissingConfiguration: Environment variable 'OPENAI_API_KEY' is not set` | No `.env`, or the kernel started before it existed. Create `.env` and restart the kernel |
| `DeepEvalError: OpenAI API key is not configured` | Same cause, surfacing from DeepEval's constructor |
| Connection refused / timeout at the health check | `AML_API_BASE_URL` is wrong or the application is not running. Check it with `curl` first |
| `HTTP 401` | The application runs with `AUTH_MODE=api_key`. Set `AML_API_KEY` |
| `HTTP 403` | The key's role is too narrow. Trace and eval endpoints need `eval_reader`; reset and MCP invoke need `test_operator` |
| `HTTP 503 llm_not_configured` | The **application** has no LLM key configured. This is the application's key, not the judge's `OPENAI_API_KEY` |
| `Seed version mismatch` | The instance was seeded differently from the goldens. Reseed it, or set `AML_EXPECTED_SEED_VERSION` if you know the goldens still hold |
| `The golden below cites clauses that are not in the document the API serves` | The policy corpus changed. The comparison already ignores line breaks and repeated spaces, so the wording itself is gone. Update the golden from the current document text |
| `The trace reports no tools_selected` on a repeat run | The planner skips tools already completed for that case. Set `AML_RESET_BEFORE_RUN=true` |
| `eval_tracing: false` | The instance was built without the `eval` extra, or runs with `ENVIRONMENT=production` (which ignores tracing entirely). Blocks `PlanAdherenceMetric` only. No longer the expected state - the reference instance reports `true` as of 2026-07-31 |
| Request timeouts on investigation endpoints | An investigation runs several tools plus an LLM call. Raise `AML_API_TIMEOUT_S` |

### Repeat runs

The application's tool planner skips any tool that already ran successfully for
a case. The first investigation after a reset is therefore the only one whose
tool plan matches the documented rules. For repeatable results:

```bash
AML_RESET_BEFORE_RUN=true uv run python run_all.py
```

This calls `POST /api/dev/reset`, which **drops and recreates every table** on
the target instance. Never point it at anything you care about.
