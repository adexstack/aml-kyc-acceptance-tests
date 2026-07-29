# Working in this repository

Orientation for a Claude Code session, not a tutorial. Read this before
proposing or building anything; it tells you what's already decided and where.

## What this repository is

A **black-box acceptance-test harness** for a live AML/KYC investigation
application. Fourteen DeepEval notebooks (`notebooks/*.ipynb`) drive the
running application over its documented HTTP API and grade one metric each.
`run_all.py` executes all of them non-interactively for CI.

It is **not** part of the application. It has:

- no import from, and no filesystem dependency on, the application's source
  tree (`aml-kyc-agentic-platform`, developed in a separate repository)
- no database access — everything comes from documented endpoints
- no assumption that it runs on the same machine, network, or team as the
  application

Copy this directory anywhere, run `uv sync`, point `AML_API_BASE_URL` at a
reachable instance, and it works. That property is deliberate and every
design doc in this repo protects it. Don't propose anything that requires an
import from the application or a shared filesystem — that isn't a small
compromise, it's a different project.

## The application, as seen from here

- Backend: `AML_API_BASE_URL` (default `http://localhost:8000`), OpenAPI at
  `/docs` / `/openapi.json`. Treat the OpenAPI spec as the source of truth
  for what's callable — don't guess at endpoints or infer internals from the
  frontend.
- Frontend: `http://localhost:3000`. Useful for understanding the product
  surface and for anything that has no API equivalent (there is currently no
  documented UI-testing capability in this repo — see
  [docs/regression-testing-plan.md](docs/regression-testing-plan.md) before
  adding one).
- The application is **live and, per the operator, production** — not a
  disposable sandbox. That materially changes what's safe to run against it:
  see "Live-application constraints" below before writing anything that
  calls it repeatedly, at volume, or destructively.

Endpoints this harness currently exercises or could reasonably exercise
(confirmed against a running instance's `/openapi.json`, 2026-07-29):
health/readiness (`/api/health`, `/api/test/health`), case and customer CRUD,
investigation (`/api/cases/{id}/investigate`), decisions and status
transitions, RAG query/retrieve, MCP discovery and invoke, eval export
(`/api/eval/export`, `/api/eval/export/{run_id}`), agent trace
(`/api/agent/trace/{run_id}`), and an end-of-day audit report
(`/api/reports/eod`). Re-check this list against `/openapi.json` before
relying on it — it will drift as the application evolves and nothing in this
repo is notified when it does.

## Documents already in this repository — read before proposing anything

| Doc | Covers | Don't re-propose |
|---|---|---|
| [README.md](README.md) | Setup, running, troubleshooting, portability rules | — |
| [docs/metric-notes.md](docs/metric-notes.md) | Per-metric implementation status, what's blocked/limited and why, judge model choice | Anything about individual DeepEval metrics — this is the log of record |
| [docs/observability-plan.md](docs/observability-plan.md) | Langfuse for eval-quality observability: test-side experiments, app-side tracing, PII/governance stance on tracing, prompt-management stance | Langfuse phasing, the "self-host for real customer data" PII call, the "no prompt management for application prompts" governance call |
| [docs/judge-calibration.md](docs/judge-calibration.md) | LLM-judge reliability: consistency, drift across model/version changes, threshold provenance | — |
| [docs/regression-testing-plan.md](docs/regression-testing-plan.md) | Score history, baseline comparison, what counts as a regression for a noisy judge | — |
| [docs/performance-latency-plan.md](docs/performance-latency-plan.md) | What black-box latency testing can and can't tell you here, using the trace endpoint's own timing data | — |
| [docs/monitoring-plan.md](docs/monitoring-plan.md) | Lightweight synthetic/canary checks distinct from Langfuse eval-observability | — |
| [docs/governance-plan.md](docs/governance-plan.md) | Audit-trail verification, decision/rationale enforcement, what governance is this repo's job vs. the application's | — |

These are living design docs, not a one-time report. When you revisit a
capability, edit its doc in place rather than writing a new one that
partially overlaps.

## The framework for judging any new test capability

Apply this explicitly to every proposal — yours or the user's. Don't let a
proposal skip a bucket because the honest bucket is inconvenient.

1. **Test-side, valuable, in scope here.** Design it, using only documented
   endpoints and no application source.
2. **Valuable, but it's the application's job.** Name exactly what the
   application would need to expose or change, and stop there — do not
   design the application-side implementation from this repository. (Example
   already on record: `PlanAdherenceMetric` needs the backend image built
   with the `eval` extra — see `metric-notes.md`.)
3. **Not valuable, or net-negative here.** Say so and say why. This is the
   bucket most proposals quietly skip. Precedent: Langfuse prompt management
   for application prompts (`observability-plan.md` §3.2) — a generally good
   feature that is a governance regression in this specific, regulated
   context.

## Constraints every doc and every notebook must respect

- **No application source, ever.** Not even read-only, not even "just to
  check a type." If you need to know a shape, read the OpenAPI schema or call
  the endpoint.
- **No filesystem dependency on the application.** Not even a shared
  `config.json` or `secrets.toml`. If you need a secret, read it from the
  environment or a `.env` file in this repo.
- **Explicitly request permission to read the application source code** If you really need to read the application source code to design a test, ask me — this is a regulated context with strict obligations.
- **The core suite stays offline-runnable.** `uv sync` plus a reachable API
  is the whole dependency list for the 14 notebooks. New capabilities that
  need a network service (Langfuse, a dashboard, a metrics store) go in a
  separate runner, never inside a notebook — see `observability-plan.md` §3.1
  for why, and follow the same pattern for anything new.
- **Every judge call costs money and time.** State a cost/cadence opinion
  with any new capability, not just a design. `metric-notes.md`'s
  `gpt-5.4-mini` default exists for exactly this reason.
- **This is a regulated AML/KYC context.** Any proposal that sends data
  outside this machine — a trace, a score, a screenshot — needs an explicit
  PII call-out, even when the honest answer is "defer to whoever owns data
  protection." See `observability-plan.md` §5 for the standard this repo
  already holds itself to.
- **Fail loudly, never infer.** If a capability needs data the API doesn't
  serve, the test raises with a specific, actionable message. It never
  reconstructs the missing piece from what it can see — see
  `metric-notes.md`'s note on `@observe` and trace export for why that
  produces numbers that look like results and measure nothing.

## Live-application constraints

The target is described as a live production system, not a disposable test
deployment. That changes the risk calculus for a few categories of test this
repo might otherwise build without a second thought:

- **`POST /api/dev/reset` drops and recreates every table.** Already gated
  behind `AML_RESET_BEFORE_RUN` and documented in the README. Never call it
  from anything that could run unattended against a shared instance.
- **Load, stress, or soak testing.** Sending sustained volume at a production
  system to find its breaking point is a different activity from timing the
  handful of calls a normal test run already makes, and needs explicit
  authorization from whoever owns that instance — it isn't something to add
  because a "performance testing" checkbox exists. See
  `performance-latency-plan.md` for where the line is drawn.
- **Anything that writes** (`investigate`, `decisions`, `status`, `dev/reset`,
  `link-transaction`, `notes`) **is a real side effect** on a real system, not
  a sandboxed call. A scheduled monitor or regression job that fires
  frequently is, cumulatively, load on production — weigh cadence
  accordingly rather than defaulting to "more frequent is safer."

## What "done" looks like for a proposal in this repo

A capability is ready to build here when you can state, in the terms above:
which bucket it's in, which documented endpoint(s) it uses, what it costs
per run, what it does to a live production instance, and what it does when
the data it needs isn't there. If you can't state all five, it isn't
designed yet — it's an idea.
