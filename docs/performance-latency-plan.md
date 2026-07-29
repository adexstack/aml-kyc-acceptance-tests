# Performance and latency testing — what a black-box test repo can honestly claim

**Status: design document. Nothing here has been installed or executed.**
Read the "Live-application constraints" section of `CLAUDE.md` first — it
matters more here than in any other doc in this set, because this is the
category most likely to accidentally hurt a real production system.

## 1. "Performance testing" is at least three different things — pick one

| Meaning | What it needs | Where it belongs |
|---|---|---|
| **Per-call latency observation** — how long does an investigation or a RAG query take, from the caller's side | Nothing beyond what this repo already does every time it makes a call | **Here** |
| **Internal profiling** — which line of code, which DB query, which model call inside the backend is slow | APM/tracing instrumentation inside the application | **Application repository** |
| **Load, stress, or soak testing** — how does the system behave at N concurrent users or sustained volume, where does it fall over | Deliberate, authorized load generation against a real system, capacity planning, usually a non-production or scaled test environment | **Not here without explicit, separate authorization** — see §4 |

This doc only covers the first. The second needs source access this repo
deliberately doesn't have. The third needs a decision this repo's operator
has to make explicitly, on a system described as live production — not
something to back into because a "performance" checkbox exists in a request.

## 2. The application already tells you more than you'd expect from outside

This is the actual finding worth acting on: **you don't need to build
client-side timing to get a real latency breakdown.** `GET
/api/agent/trace/{run_id}` (confirmed against a running instance's
`/openapi.json`, 2026-07-29) returns:

- `latency_ms` on the run as a whole
- `steps[]`, each with a `type` (`retrieval` / `tool_call` / `synthesis`),
  `status`, and its own `latency_ms`

That's a server-side phase breakdown, already served, for every investigation
this suite runs. No instrumentation to add, no application change, nothing
blocked. This is a stronger signal than client-observed wall-clock time,
because it isolates "the backend spent 340ms retrieving" from "the network
plus this notebook's own overhead added 900ms on top."

## 3. What to build, ordered by value

### Phase 0 — Record what every run already produces (no application change, near-zero cost)

Every investigation this suite triggers already returns `latency_ms` per
step. Capture it in the same history mechanism as
`regression-testing-plan.md` Phase 0 (one more column, not a new system):
run id, total `latency_ms`, per-phase `latency_ms`, judge model used for
that notebook. This is observation of calls you were making anyway — it adds
no new load to the application.

**Do this before anything below.** It's free, it uses data that already
exists, and it will tell you whether phases 1–2 are even worth doing (if
latency is already stable and boring, stop here).

### Phase 1 — Percentile tracking on the calls this suite already makes

Once Phase 0 has a few runs of history, report p50/p95 per endpoint and per
phase, not just the last run's number. A single investigation's timing is
one sample; whether 800ms is normal or an outlier needs a distribution. This
is pure analysis over Phase 0's data — no new calls against the application.

### Phase 2 — A small, explicitly-scoped synthetic latency check

If you want latency signal *between* full suite runs (not just when the
regression suite happens to execute), a narrow script that calls only
read-only, idempotent, cheap endpoints — `GET /api/health`, `GET
/api/test/health`, `GET /api/eval/scenarios` — on a schedule, at low
frequency (minutes, not seconds), reporting simple wall-clock latency.

Deliberately excluded from this phase: `POST /api/cases/{id}/investigate`
and anything else that does real LLM work or writes state. Hitting those on
a timer is both a real cost (LLM spend, per `judge-calibration.md`'s cost
framing) and real, repeated load on a production system for a benefit
(catching slow investigations sooner) that Phase 0 already gets you for
free, from runs you're making anyway. This overlaps in spirit with
`monitoring-plan.md`'s synthetic checks — if you build both, they should be
the same script, not two.

## 4. Load, stress, and soak testing — deliberately out of scope here

Sending sustained or concurrent volume at `/api/cases/{id}/investigate` to
find a breaking point, measure throughput, or validate autoscaling is a
fundamentally different activity from timing the calls a normal test run
makes, and it's the one category in this whole document set where doing it
without asking first is a real risk:

- **The target is live production**, per how this framework was described.
  Generating deliberate load against it is not something to infer
  authorization for from "the plan mentioned performance testing" — get an
  explicit yes from whoever owns that instance, scoped to a time window, the
  same way you'd never run `POST /api/dev/reset` (which drops every table)
  against anything you don't own outright.
- **The investigation planner has documented state**: it skips tools already
  completed for a case (README, "Repeat runs"). Concurrent investigate calls
  against the same case will produce inconsistent tool plans for reasons
  that have nothing to do with the system's capacity — so even if
  authorized, a load test here needs its own case-management strategy (fresh
  cases per request, or `AML_RESET_BEFORE_RUN` semantics), or the results
  will be noise, not throughput data.
- **If this is genuinely needed**, it belongs against a staging or
  load-testing environment sized and authorized for it, using a real load
  tool (k6, Locust, Gatling) — not something to bolt onto a DeepEval
  notebook suite. That's infrastructure this repository doesn't own and
  shouldn't grow.

## 5. What not to do

### Don't conflate notebook wall-clock time with application latency

`run_all.py`'s per-notebook seconds include kernel startup, the judge LLM
call, and this harness's own overhead — not just the application's response
time. A notebook taking 34 seconds does not mean the application took 34
seconds. Use the trace endpoint's `latency_ms` for application-side timing,
`run_all.py`'s timer for "how long does my CI job take," and don't report
one number as if it were the other.

### Don't build APM-style internal profiling from outside

You cannot see which database query or model call inside a step was slow —
only that the step labeled `tool_call` took 1200ms. Wanting finer-grained
internal timing is a legitimate ask, and the answer is the same as
`PlanAdherenceMetric`'s: it's an application-side instrumentation change
(the app already emits `@observe`-decorated spans per `observability-plan.md`
Phase 2 — that's where step-internal timing would come from, not from this
repository).

### Don't add a load-testing capability because this document exists

The existence of a "performance testing" section in a request is not
authorization to generate load against production. If that need is real,
it's a separate, explicit conversation with whoever owns the instance, not a
line item to check off here.

## 6. Honest summary

**Genuinely valuable, cheap, do first:** capturing the phase-level
`latency_ms` breakdown the application already serves on every run (Phase 0)
— this is real signal, already paid for, currently thrown away.

**Valuable, low cost:** percentile tracking over that history (Phase 1); a
narrow, read-only synthetic latency check on a slow cadence (Phase 2), which
should share code with `monitoring-plan.md` rather than duplicate it.

**Out of scope for this repository, and risky to add without explicit
sign-off:** load, stress, or soak testing against the live application —
name it as its own decision for the instance's owner, don't fold it into
routine test-suite growth.

**Not something this repo can do regardless of authorization:** internal
profiling of what's slow inside a step. That requires application-side
instrumentation and belongs in `observability-plan.md` Phase 2's territory,
not here.
