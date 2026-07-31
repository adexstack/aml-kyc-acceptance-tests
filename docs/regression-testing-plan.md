# Regression testing — catching change over time, not just at a point

**Status: design document. Nothing here has been installed or executed.**
Read `observability-plan.md` first if you haven't — its Phase 1 (test-side
Langfuse experiments) already solves the hard part of this problem for
anyone willing to take the dependency. This doc is for the gap before that
decision is made, and for the analysis Langfuse doesn't do for you even once
you have it.

## 1. What "regression" means for this suite today, and why that's thin

Right now, a regression test is: run the suite, read `14/14 notebooks
passed`, done. That tells you today's state. It cannot tell you:

- whether a score that's still above threshold nonetheless dropped
  meaningfully from last week
- whether a failure is new or has been failing the same way for a month
- whether "the application got worse" is even the right explanation, versus
  the judge (`judge-calibration.md`), the seed data, or a stale golden

`run_all.py` is explicit that it's a runner, not a history store — it prints
and exits, and nothing persists between invocations. That's a correct
design for what it is. It's also the actual gap: **there is currently no run
history anywhere**, so "regression testing" beyond pass/fail-today isn't
possible yet, with or without Langfuse.

## 2. Three different things can make a score change — separate them

A regression check that can't tell these apart will misattribute every
change and erode trust in the whole suite fast.

| Cause | Signature | How to tell |
|---|---|---|
| **Application actually regressed** | Score drop correlated with an app deploy/version | Compare against `TraceResponse.model` / `model_configuration` / `prompt_version`, already returned by `GET /api/agent/trace/{run_id}` — a real per-run version fingerprint, no app change needed to get it |
| **Judge noise** | Score drop within the variance band `judge-calibration.md` Phase 1 measures, no version change | Compare against that band before calling anything a regression |
| **Environment drift** | Seed data or policy corpus changed under the goldens | Already partially guarded: `AML_EXPECTED_SEED_VERSION` and `schema_version` checks fail loudly at notebook start. Golden text staleness (documented in `metric-notes.md` for `ContextualPrecision`/`Recall`) is not yet guarded automatically |

The practical implication: a regression report needs the judge-noise band
and the version fingerprint sitting next to the score, or it's just a number
that moved.

## 3. What to build, ordered by value

### Phase 0 — Persist what you already compute (no application change, do this regardless of Langfuse)

Every notebook already produces a score, a reason, and (via
`GET /api/agent/trace/{run_id}`) a `run_id`, `model`, `prompt_version`, and
`created_at`. None of it is written anywhere durable today. Before anything
fancier: have `run_all.py` (or a sibling script) append one row per
metric-run to a flat file — CSV, JSONL, or SQLite, doesn't need to be
clever — with metric name, score, threshold, pass/fail, `run_id`,
`prompt_version`, `model`, judge model, timestamp, and suite git commit if
you're running from a checkout.

This alone answers "did this get worse since last Tuesday" via a `diff` or a
five-line pandas script. It requires no new infrastructure, no credentials,
no network dependency — it's the offline-portable equivalent of Langfuse
Phase 1, and it's worth having even if you adopt Langfuse later, because CI
artifacts you can `grep` without a dashboard login are useful in their own
right.

### Phase 1 — Tolerance bands, not exact thresholds

Once Phase 0 gives you history, define "regression" as: score dropped by
more than [variance band from `judge-calibration.md` Phase 1] compared to
the trailing baseline (e.g., median of the last 5 runs), not "score is lower
than last time" or "score crossed 0.5." A metric bouncing within its known
noise band is not a regression and flagging it as one trains people to
ignore the alert.

**This depends on `judge-calibration.md` Phase 1 existing first** — you
can't set a sane tolerance band without knowing the noise floor.

### Phase 2 — Golden staleness as its own check, not a silent misattribution

`metric-notes.md` already documents that `ContextualPrecision`/`Recall`
goldens can go stale when the served policy document changes wording. Right
now each notebook discovers this itself, mid-run, if the golden's cited text
no longer appears. Make this a named pre-flight class of failure in whatever
consumes Phase 0's history — "golden mismatch," not "regression" — so a
policy-document edit doesn't get logged as the application getting worse.

### Phase 3 — Decide whether Langfuse is worth adopting for this, using Phase 0 as the baseline

`observability-plan.md` Phase 1 gives you dashboards, run comparison, and a
shared UI for free once adopted — genuinely more than a flat file gives you.
The honest trigger for adopting it specifically for regression tracking:
Phase 0's flat file becomes annoying to query, or someone other than the
person who ran the suite needs to see the trend. If that's not true yet,
Phase 0 is not a stepping stone you'll throw away — it's a legitimate
stopping point.

## 4. What not to do

### Don't build a trend dashboard in this repository

Once you have history (Phase 0), the temptation is a local web UI for
charts. That's product surface, not test infrastructure, and
`observability-plan.md` already covers the tool built for exactly this
(Langfuse). Building a bespoke one here is maintaining a second product
nobody asked for.

### Don't gate CI on raw score deltas before Phase 1 exists

A CI check that fails the build any time a score moves down at all, before
you know the noise band, will cry wolf on ordinary judge variance and get
disabled within a month. Land Phase 0 and `judge-calibration.md` Phase 1
before wiring anything into a required CI gate.

### Don't run the full regression suite per-commit

Same cost logic as `observability-plan.md` §6: every run costs real judge
spend, and several metrics judge per-chunk. Nightly or on-demand is
affordable; per-commit isn't, for the same reason `gpt-5.4-mini` is the
default judge. If you need faster feedback on one specific metric during
active work on it, run that one notebook, not the suite.

### Don't let `PlanAdherenceMetric`'s unblocking pollute a baseline

**Unblocked 2026-07-31** (`metric-notes.md`) — it now scores. The original
warning here was that no history mechanism fixes a metric with no data to
score against; that's resolved.

The live concern is now the opposite one. A metric that has just come online
will look like a dramatic improvement against any earlier baseline, because
the comparison is between "not scored" and "scored" — not between two
measurements of quality. **Start its history fresh** rather than reading a
delta across the unblock boundary, and treat its first few runs as
establishing a baseline, not as evidence of a change.

## 5. Honest summary

**Genuinely valuable, do regardless of anything else:** persisting scores
somewhere durable (Phase 0) — the current setup has zero memory between
runs, which is the actual blocker, not a lack of sophistication.

**Valuable, but sequenced behind other docs:** tolerance-band regressions
need `judge-calibration.md`'s variance measurement first; golden-staleness
detection is cheap once named as its own failure class.

**Not valuable here:** a bespoke dashboard (Langfuse already is one, if you
want one); per-commit full-suite gating; treating this as solved by adopting
Langfuse without first knowing what "regression" should mean, since Langfuse
gives you comparison, not judgment about what counts as a real regression.

**Depends on a decision only you can make:** whether Phase 0's flat file is
good enough long-term or a stepping stone to Langfuse — that's a
team-and-audience question (`observability-plan.md` §6 has the fuller
version of this trade), not a technical one.
