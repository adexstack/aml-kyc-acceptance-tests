# Judge calibration — is the judge itself trustworthy?

**Status: design document. Nothing here has been installed or executed.**
Read this alongside `metric-notes.md`, which records *what* each metric
measures and where it's blocked. This doc is about a different question:
*how much should you trust the number the judge produces*, independent of
whether the metric is well-designed.

## 1. The gap this fills

`metric-notes.md` verifies that all fourteen metric classes exist, are
implemented, and score against real API responses. It does not answer
whether `AnswerRelevancyMetric` returning `0.83` today and `0.71` next week
means the application got worse, or means `gpt-5.4-mini` scored the same
answer differently on two different days. Every metric in this suite except
`ToolCorrectnessMetric` depends on an LLM judge, and an LLM judge is not a
fixed function — it has run-to-run variance and it changes underneath you
when the provider updates the model.

This is squarely a test-side concern. It needs no application change and no
application data beyond what the suite already pulls.

## 2. What's actually at risk

| Risk | Concrete failure mode |
|---|---|
| **Run-to-run variance** | Same input, same application version, judge returns a materially different score because LLM output isn't deterministic even at low temperature |
| **Silent model drift** | `gpt-5.4-mini` behind the API changes behavior (provider-side update) with no version bump you'd notice; scores shift and you attribute it to the application |
| **Threshold provenance** | Every metric uses DeepEval's documented default of `0.5` (`metric-notes.md`). Nobody has verified that `0.5` is the right bar for *this* application's risk tolerance — it's a library default, not a calibrated decision |
| **Judge/human disagreement** | The judge might be reliably wrong in a way that's consistent — confidently scoring a subtly incorrect AML recommendation as relevant and non-hallucinatory, because "relevance" and "faithfulness to a corpus" are not the same thing as "correct regulatory judgment" |

The first two are precision problems (is the number stable). The second two
are validity problems (is the number measuring the right thing at the right
bar). Both matter; they need different tests.

## 3. What to build, ordered by value

### Phase 1 — Repeat-run variance, one metric, one scenario (cheap, do this first)

Run the same notebook's core assertion N times (5–10) against the same
application state (no reset between runs, same case) and record the score
distribution. This needs nothing new from the application — just running an
existing notebook body in a loop instead of once, with results written
somewhere durable instead of printed once.

- If the spread is tight (say, within 0.05 for a metric on a 0–1 scale),
  point-in-time scoring is a reasonable proxy and you can stop worrying about
  variance day-to-day.
- If it's wide, a single run is not a reliable regression signal on its own,
  and any regression-testing gate (`regression-testing-plan.md`) needs to
  compare distributions or medians-of-N, not single points.

**Do this for `AnswerRelevancyMetric` or `HallucinationMetric` first** — they
run once per call, unlike `ContextualPrecision`/`Recall`, which already judge
per-chunk and are more expensive to repeat N times.

### Phase 2 — Record judge model identity with every score (cheap, mechanical)

`metric-notes.md` already documents the deliberate `gpt-5.4-mini` deviation
from `gpt-5.4`. That's a point-in-time decision recorded in prose. Once you
have any score history at all (see `regression-testing-plan.md`), every
stored score needs `DEEPEVAL_JUDGE_MODEL` attached as metadata, not just
mentioned in a doc. A score without its judge model attached is not
comparable to anything, including itself six months later.

This is not a new capability, it's a field you must not forget to persist
once Phase 1 of the regression-testing plan exists. Listed here because it's
easy to build the history table first and realize the gap after the fact.

### Phase 3 — Threshold sanity check against a small human-labeled set (real effort, real value)

Take a handful of past investigation outputs (5–10 is enough to start) and
have a domain person — not an engineer — label them pass/fail on the
dimension a metric claims to measure (e.g., "does this recommendation cite
real, applicable clauses" for `HallucinationMetric`/`ContextualPrecision`).
Compare judge scores against those labels.

This is the only phase that can answer "is `0.5` the right threshold for a
compliance product," and it's genuinely worth doing once — you are not going
to loop this into CI, it's a periodic sanity check, not a per-run gate.

**Be honest about what this buys you**: 5–10 labels is enough to catch a
badly miscalibrated threshold (judge passes things a human clearly rejects,
or the reverse). It is not enough for a statistically defensible agreement
rate — don't report a kappa score off n=8 and treat it as settled.

## 4. What not to do

### Don't build a second LLM judge to grade the first judge's reasons

It's tempting — DeepEval scores give you a `reason` string, and you could
have a second model critique the reason. This adds another non-deterministic
LLM call to validate a non-deterministic LLM call, doubles judge cost, and
doesn't resolve the actual open question (is the *threshold* right), which
only a human calibration pass (Phase 3) can answer. Skip it.

### Don't chase every metric's variance before you've checked one

Phase 1 done once, for one or two metrics, tells you whether variance is a
suite-wide property (likely, since they share a judge model and similar
prompting patterns) or metric-specific. Don't repeat the full N-run
experiment across all fourteen before that question is answered.

### Don't treat "gpt-5.4 vs gpt-5.4-mini" as a calibration question

That's a cost/latency trade, already recorded honestly in `metric-notes.md`.
Running the same calibration work under both models would tell you whether
scores are comparable across the two — worth knowing eventually, not part of
this doc's scope, and not a substitute for Phase 3's human-labeled check
under whichever model you actually run in practice.

## 5. Honest summary

**Genuinely valuable:** knowing whether a score swing between two runs is
signal or judge noise (Phase 1); attaching judge-model identity to every
stored score before you have years of incomparable history (Phase 2); one
real check of whether `0.5` means anything for this domain (Phase 3, done
once, not continuously).

**Not valuable here:** a second judge grading the first judge's reasoning; an
automated, continuous, statistically rigorous agreement-rate pipeline —
that's a research program, not a practical addition to a 14-notebook
acceptance suite, and the honest tool for "is this threshold defensible" at
this scale is a person looking at ten examples, not more infrastructure.

**Depends on a decision only you can make:** who plays the "domain person" in
Phase 3, and how often (once? yearly? after every threshold change?) it's
worth repeating. Pin that down before scheduling it as an ongoing practice
rather than a one-time check.
