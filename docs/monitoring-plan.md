# Monitoring — is the application there and healthy, independent of any test run

**Status: design document. Nothing here has been installed or executed.**
This is a narrower, cheaper thing than `observability-plan.md`, and the two
are easy to conflate. Read the distinction in §1 before building either.

## 1. This is not the same problem `observability-plan.md` solves

| | `observability-plan.md` | This doc |
|---|---|---|
| Question it answers | "How good are the application's answers, and is that changing?" | "Is the application reachable and internally consistent, right now?" |
| Trigger | You run the acceptance suite | Anything — a schedule, a pre-flight check, an on-call page |
| Signal | DeepEval scores | HTTP reachability, `schema_version`, `seed_version`, DB/Chroma reachability |
| Needs a judge model | Yes, for most metrics | No |
| Needs an application change | Only for Phase 2 (tracing) | No |

If you only build one, build this one first — it's cheaper, needs no judge
model, and every notebook already depends on its signal at pre-flight
(`README.md`'s health-check cell). What's missing is making that signal
available *without* having to run a 14-notebook suite to get it.

## 2. What the application already exposes for exactly this

Two endpoints, confirmed against a running instance's `/openapi.json`
(2026-07-29), already do the job:

- **`GET /api/health`** — `status`, `schema_version`, `eval_tracing`. This is
  what every notebook already checks before running.
- **`GET /api/test/health`** — explicitly documented as *"Pre-flight for
  test/eval callers: confirms DB and Chroma are reachable"*, returning
  `status: ok | degraded` plus a `checks` object. This is a purpose-built
  aggregate signal for exactly this use case — **don't try to build your own
  deeper check of "is the database up" from outside**; the application has
  already decided what counts as healthy and told you in one field. Going
  further (trying to infer DB latency, connection pool state, etc.) means
  guessing at internal architecture this repo has no business assuming.

## 3. What to build, ordered by value

### Phase 0 — A canary that runs on a schedule, not just when someone runs the suite (no application change)

A small script — a few lines, shares nothing with the notebooks — that calls
`GET /api/health` and `GET /api/test/health` on a schedule (minutes to low
hours, this is a cheap read-only call) and reports: reachable or not,
`status`, `schema_version`, `seed_version` if surfaced, `eval_tracing`. This
is the same script `performance-latency-plan.md` Phase 2 describes for
latency sampling — **build one script that does both**, not two that happen
to call the same endpoints.

**What this catches that running the suite doesn't**: the suite only checks
health when someone runs it. If the application goes down, or its schema
drifts, at 2am on a day nobody runs the notebooks, nobody finds out until
the next scheduled full run. A canary closes that gap for the cost of two
HTTP calls every few minutes.

### Phase 1 — Alert on drift, not just on downtime

`status: degraded`, an unexpected `schema_version`, or a `seed_version`
change are all things the notebooks already treat as hard stops
(`AML_EXPECTED_SEED_VERSION`, `README.md`). A canary should flag the same
conditions as soon as they appear, rather than the first person running the
suite discovering it via a notebook exception. Route this wherever your team
already gets alerts (Slack webhook, email) — **don't build a paging or
incident-management system in this repository**; that's a production-ops
concern with its own tooling, and this repo's job ends at "notice and say
something," not "own the on-call rotation."

### Phase 2 — A minimal historical record of uptime/reachability

Once Phase 0 runs on a schedule, the same history mechanism as
`regression-testing-plan.md` Phase 0 can hold a reachability log: is this
worth doing depends entirely on whether anyone asks "was the app up during
the incident on Tuesday" — a question this repo can answer cheaply if it's
already recording, and can't answer at all if it isn't. Don't build this
speculatively; build it once someone asks that question the first time, or
skip it.

## 4. What not to do

### Don't try to monitor what's inside the application

CPU, memory, DB connection pool depth, query latency, autoscaling behavior —
none of this is visible from outside, none of it should be guessed at, and
all of it is the production operator's job with production-grade tooling
(Datadog, Grafana, whatever they already run against the real
infrastructure). `GET /api/test/health`'s `checks` object is the aggregate
signal the application has chosen to expose; that's the ceiling for what a
black-box monitor can honestly claim to know.

### Don't duplicate `observability-plan.md`'s dashboard

If you adopt Langfuse for eval-quality tracking, do not also build a second,
separate dashboard here for uptime — either fold reachability into the same
tool (Langfuse supports custom scores/metadata if you want one pane of
glass) or keep this as a lightweight script with no UI at all. A second
half-built dashboard is worse than no dashboard.

### Don't run the canary at investigate-endpoint frequency

Phase 0's canary only calls read-only, cheap endpoints. Do not extend it to
periodically call `POST /api/cases/{id}/investigate` "just to be sure the
whole pipeline works" — that's real LLM spend and real write-side load on a
live production system, on a timer, for a signal `GET /api/test/health`'s DB
and Chroma reachability check already gives you more cheaply. If you
genuinely need "can it complete an investigation right now" as a monitored
signal, that's a deliberate, low-frequency (daily, not every few minutes)
decision to make explicitly — not a default.

## 5. Honest summary

**Genuinely valuable, cheap, do first:** a scheduled canary against
`/api/health` and `/api/test/health` (Phase 0) — the application already
built the exact signal this needs; the gap is only that nothing calls it
except a full notebook run.

**Valuable once Phase 0 exists:** alerting on drift, not just downtime
(Phase 1); a reachability history, but only once someone actually needs to
answer "was it up" after the fact (Phase 2) — don't build this speculatively.

**Not this repo's job, regardless of effort available:** internal
infrastructure monitoring, paging/incident management, and periodic
full-pipeline (`investigate`) canaries — each of those either assumes
architecture this repo shouldn't assume, duplicates tooling that belongs to
production ops, or adds real cost/load for a signal cheaper endpoints
already provide.
