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
# {"status":"ok","schema_version":"1.0.0","eval_tracing":false}
```

Every notebook performs this check itself in its configuration cell and stops
with a specific message if the application is unreachable, the schema version
differs, or the seed version does not match the one the goldens were authored
against.

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
13/14 notebooks passed
  FAILED  PlanAdherenceMetric.ipynb: RuntimeError: The application reports eval_tracing: false, ...
```

**13 of 14 passing is the current expected result.** `PlanAdherenceMetric` fails
by design on an application instance that does not expose agent traces - see
below. Inside each notebook, expect the request, the raw response, the mapped
DeepEval fields, the derived golden, and then the score with the judge's reason.

A metric scoring below its threshold is a **reported result, not a crash**: the
notebook prints the score, the pass/fail verdict and the reason. Notebooks raise
only when the test itself cannot be conducted honestly - unreachable API, schema
or seed mismatch, missing trace data, or a golden that no longer matches the
served corpus.

## Blocked and limited metrics

| Metric | State |
|---|---|
| `PlanAdherenceMetric` | **Blocked.** Needs the agent's declared plan, which requires `eval_tracing: true`. The deployed backend image does not install the `eval` extra, so `deepeval` is absent, `configure_tracing()` returns false, and `GET /api/agent/trace/{run_id}` serves `deepeval_trace: null`. Fixing this is an application change |
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
uv sync --extra langfuse
uv run python run_experiment.py                    # all 14 metrics
uv run python run_experiment.py tool_correctness    # substring filter on experiment names
```

Needs `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in `.env`, in addition
to the variables above. It applies the same PII-masking function to
everything it sends and refuses to run unless `AML_API_BASE_URL` resolves to
an instance serving the known synthetic seed data — see
`docs/observability-plan.md` §5 for the PII stance this repo holds itself to.

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
`plan.md` §1.7 tracks the current state (as of 2026-07-29, a foreground
`./run.sh`) and the recommended move to `./svc.sh install && ./svc.sh
start` so the runner survives closing the terminal instead of needing to be
started by hand before every dispatch.

## How trace-dependent metrics obtain internal data

Metrics needing more than the final answer - invoked tools, arguments, MCP
calls, retrieval context - read it from documented endpoints only:

| Data needed | Source |
|---|---|
| Tools called and their arguments | `GET /api/eval/export/{run_id}` (`tools_called[]`) |
| Executed step timeline | `GET /api/agent/trace/{run_id}` (`steps[]`) |
| Declared plan | `GET /api/agent/trace/{run_id}` (`deepeval_trace`) - **currently null** |
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
| `eval_tracing: false` | Expected on an instance without the `eval` extra installed. Blocks `PlanAdherenceMetric` only |
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
