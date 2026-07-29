# Langfuse for acceptance-test evaluation — a practical plan

**Status: design document. Nothing here has been installed or executed.** It is
based on the Langfuse documentation (read 2026-07-29) and on the actual code in
this repository. Every code sample is illustrative and needs to be verified
against the version you pin — the Python SDK moved to an OpenTelemetry-based
API and older tutorials on the web show a materially different interface.

Read the "What not to do" section before the implementation sections. The most
valuable advice in this document is about what to leave alone.

---

## 1. The one distinction that governs everything

Langfuse can be attached at two completely different places, and conflating
them is the most common way this kind of project goes wrong.

| | **A. Application-side** | **B. Test-side** |
|---|---|---|
| What it instruments | The FastAPI backend: agent runs, retrieval, LLM calls, MCP tools | The acceptance-test harness that calls the API |
| Who owns it | Backend engineering | AI Quality Engineering |
| What you see | Internal span tree, token cost, latency per step | Test inputs, application outputs, metric scores |
| Requires app change | **Yes** | **No** |
| Breaks the black-box contract | N/A — it is the app | **Only if done carelessly** — see §3 |

These deliver different value and carry different risk. Decide which you are
doing before writing any code. My recommendation is **B first, A later, and
possibly never both linked** — reasoning in §6.

---

## 2. What Langfuse actually gives you

From the docs, the product has four areas. Their value *to this project
specifically* varies enormously.

| Area | Value here | Why |
|---|---|---|
| **Evaluation / Datasets / Experiments** | **High** | This is the real win. Your suite currently produces a pass/fail line in a terminal and nothing else. Langfuse turns each run into a persistent, comparable record: score per metric per scenario, tracked across runs, diffable between versions |
| **Observability / Tracing** | **Medium-high** | Genuinely useful for debugging *why* a metric regressed, and for cost/latency tracking. But it requires application changes and carries the PII exposure in §5 |
| **Scores** | **High** | The mechanism that makes the above work. DeepEval computes a number; Langfuse stores it against a trace and a run, so you get history |
| **Prompt management** | **Low, arguably negative** | See §3.2. In a compliance product this is a governance risk, not a feature |

### What Langfuse does *not* replace

**Langfuse is not a metric library.** Its built-in LLM-as-a-judge evaluators
cover generic dimensions — relevance, hallucination, toxicity, correctness.
Nothing in it corresponds to `ToolCorrectnessMetric`, `ArgumentCorrectnessMetric`,
`PlanAdherenceMetric`, or `MultiTurnMCPUseMetric`, which are the metrics that
actually test *agentic* behaviour and are the reason DeepEval was chosen.

So: **keep DeepEval as the scorer, add Langfuse as the store and the
dashboard.** Anyone proposing to replace DeepEval with Langfuse's evaluators is
proposing to delete your tool-calling coverage. The two are complements, and the
integration point between them is a single number plus a comment string.

---

## 3. What not to do

### 3.1 Do not put Langfuse inside the 14 notebooks

The notebooks are the deliverable, and their defining property is that they are
self-contained and portable: no imports from the application, no shared local
helper package, runnable after being copied into another repository with
`uv sync` alone.

Adding Langfuse calls to all fourteen would mean:

- a hard dependency on a network service in every notebook, so the suite stops
  working offline or in an air-gapped review environment;
- credentials (`LANGFUSE_SECRET_KEY`) required to run a test that does not
  logically need them;
- the same integration boilerplate copy-pasted fourteen more times, in files
  that already repeat their config block by design.

**Instead, add a separate runner** alongside `run_all.py` that imports nothing
from the notebooks and drives the API directly. The notebooks stay as the
readable, portable, dependency-light artefact; the Langfuse runner becomes the
CI/reporting path. Both call the same endpoints and derive goldens the same way.

This costs some duplication of the golden-derivation logic. That is a real cost
and I am not going to pretend otherwise — but it is smaller than the cost of
making every notebook depend on a SaaS.

### 3.2 Do not adopt Langfuse prompt management here

The docs make a strong case for it in general: non-technical staff edit prompts
in a UI, the app fetches the latest version at runtime, no deploy needed. That
is exactly the wrong property for this application.

- **This is a regulated decision-making system.** The prompts shape AML risk
  assessments and escalation recommendations. A UI edit that takes effect
  without code review, without a diff, and without a test run is a governance
  hole. Your current arrangement — prompts in code, versioned (`rag_answer_v4`),
  changed via pull request — is *better* for this domain, not worse.
- **It breaks acceptance-test reproducibility.** If the app fetches its prompt
  at runtime, a test run's result depends on what the prompt happened to be at
  that moment. Re-running the same suite against the same code could produce
  different scores. Your suite already guards seed-data drift with
  `AML_EXPECTED_SEED_VERSION`; runtime-mutable prompts reintroduce exactly that
  class of silent drift on a dimension you cannot version-check as easily.
- **It adds a runtime dependency on an external service** to the request path.
  The SDK caches client-side, which mitigates latency and brief outages, but you
  have now coupled production behaviour to a third party for no benefit you
  cannot get from a code constant.

**Use it only for the judge prompts in your evaluation pipeline**, if at all —
those are test-side, not product-side, and iterating on a judge rubric in a UI
is genuinely convenient. Even then, pin a version label for any run you intend
to compare against history.

### 3.3 Do not expect Langfuse to unblock `PlanAdherenceMetric`

It will not. That metric is blocked because the backend image omits the `eval`
extra, so `deepeval` is not importable, `configure_tracing()` returns `False`,
and `GET /api/agent/trace/{run_id}` serves `deepeval_trace: null`
(`docs/known-issues.md`). `PlanAdherenceMetric` consumes **DeepEval's own trace
format**. Langfuse traces are a different shape and will not satisfy it.

Langfuse lets you *sidestep* the blocker by writing your own plan-adherence
check as a code evaluator over Langfuse spans — but that is a different metric
you would own and maintain, not the DeepEval one. The one-line fix
(`uv sync --frozen --no-dev --extra eval` in the backend image) remains the
cheapest path to the requested metric.

---

## 4. Recommended architecture

Three phases, ordered by value-per-unit-effort. **Phase 1 alone delivers most of
the benefit and requires zero application changes.** Stop there if the rest does
not earn its keep.

### Phase 1 — Test-side experiments (no application change)

Map your existing suite onto Langfuse's experiment model:

| Langfuse concept | Your equivalent |
|---|---|
| Dataset | The 8 seed scenarios from `GET /api/eval/scenarios` (`s1`..`s8`) |
| Dataset item | One scenario: `input` = the investigation task, `expected_output` = the derived golden |
| Task | Call the application API and return its answer |
| Evaluator | A DeepEval metric, wrapped to return a Langfuse `Evaluation` |
| Dataset run | One execution of the suite, tagged with the application version |
| Score | The metric's numeric result plus its reason string |

Sketch — verify against your pinned SDK version:

```python
# acceptance-tests/run_experiment.py  (illustrative)
import os, httpx
from langfuse import Evaluation, get_client
from deepeval.metrics import AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase

langfuse = get_client()
API_BASE = os.environ.get("AML_API_BASE_URL", "http://localhost:8000")

scenarios = httpx.get(f"{API_BASE}/api/eval/scenarios", timeout=30).json()
data = [
    {
        "input": {"case_id": s["case_id"], "scenario_id": s["scenario_id"]},
        "expected_output": None,          # derived per metric, see the notebooks
        "metadata": {"title": s["title"], "seed_version": s["seed_version"]},
    }
    for s in scenarios
]

def task(*, item, **kwargs):
    case_id = item["input"]["case_id"]
    r = httpx.post(f"{API_BASE}/api/cases/{case_id}/investigate", timeout=180)
    r.raise_for_status()
    body = r.json()
    # run_id is the correlation key back to the application's own trace - see §4.3
    return {"answer": body["actual_output"], "run_id": body["run_id"]}

def answer_relevancy(*, input, output, **kwargs):
    metric = AnswerRelevancyMetric(threshold=0.5, async_mode=False)
    tc = LLMTestCase(input=str(input), actual_output=output["answer"])
    metric.measure(tc)
    return Evaluation(name="answer_relevancy", value=metric.score,
                      comment=metric.reason)

def mean_relevancy(*, item_results, **kwargs):
    vals = [e.value for r in item_results for e in r.evaluations
            if e.name == "answer_relevancy" and e.value is not None]
    return Evaluation(name="mean_answer_relevancy",
                      value=sum(vals) / len(vals) if vals else None)

result = langfuse.run_experiment(
    name="aml-acceptance",
    data=data,
    task=task,
    evaluators=[answer_relevancy],
    run_evaluators=[mean_relevancy],
    max_concurrency=2,          # the app does real LLM calls; do not hammer it
    metadata={"schema_version": "1.0.0", "judge_model": os.environ.get("DEEPEVAL_JUDGE_MODEL")},
)
print(result.format())
langfuse.shutdown()             # required in short-lived processes or you lose data
```

**What this buys immediately:** every run is stored and comparable. You can
answer "did tool-calling accuracy drop when we changed the planner?" — which
your current terminal output cannot answer at all, because it keeps no history.

**`max_concurrency` deserves care.** Each item triggers a real investigation:
retrieval, several MCP tool calls, an LLM synthesis. The planner also *skips
tools already completed for a case*, so concurrent runs against the same case
will produce different tool sets. Keep concurrency low and reset between runs
(`AML_RESET_BEFORE_RUN=true` semantics) or your scores will be noisy for reasons
that have nothing to do with quality.

### Phase 2 — Application-side tracing (application change)

Instrument the backend so you can see *inside* a run. The natural insertion
points already exist, because the code is already decorated:

| Existing decorator | File |
|---|---|
| `@observe(type="agent", name="investigation")` | `backend/app/services/agent/investigation.py:136` |
| `@observe(type="llm", name="synthesis")` | `backend/app/services/agent/investigation.py:283` |
| `@observe(type="tool")` | `backend/app/services/agent/investigation.py:486` |
| `@observe(type="agent", name="rag_query")` | `backend/app/services/rag/qa.py:104` |
| `@observe(type="llm", name="generation")` | `backend/app/services/rag/qa.py:91` |
| `@observe(type="retriever")` | `backend/app/services/rag/retrieval.py:39` |

**Important nuance:** `backend/app/observability.py:70` defines the project's
*own* `observe`, deliberately wrapping DeepEval's rather than applying it
directly. Langfuse also ships an `@observe`. Do not import Langfuse's decorator
into these modules directly — extend the existing indirection instead, so one
wrapper can emit to DeepEval, Langfuse, both, or neither depending on
configuration. That keeps the choice in one file and preserves the existing
`EVAL_TRACING` switch.

Budget this honestly: it is a real backend change touching the agent hot path,
needs its own tests, and adds a dependency to the production image. It is worth
doing when you are debugging quality regressions often enough that "I can see
the span tree and token counts" pays for itself.

### Phase 3 — Linking test scores to application traces

This is where people expect magic and get frustrated. Two options:

**Option A — correlate by `run_id` (no application change, recommended).**
The API already returns a `run_id`, and `GET /api/eval/export/{run_id}` and
`GET /api/agent/trace/{run_id}` are keyed on it. Put it in Langfuse metadata
(as the sketch above does). You now have a join key between a Langfuse score and
the application's own record. Not a unified flame graph, but it answers "which
application run produced this score", which is 90% of the practical need.

**Option B — propagate W3C trace context (application change).**
The SDK supports joining an existing trace via `trace_context={"trace_id": ...,
"parent_span_id": ...}`, and `create_trace_id(seed=...)` produces deterministic
ids from an external key. For a genuinely unified trace the backend must accept
an inbound `traceparent` header and continue the trace rather than starting its
own. That is an application change, and it means a test client can influence
server-side trace identity — think about whether you want that on any
internet-facing deployment.

Do Option A. Revisit B only if you find yourself repeatedly unable to answer a
real question without it.

---

## 5. The honest problem: PII

**This is the part to settle before any of the above.**

This application handles AML/KYC data: customer names, dates of birth,
nationalities, transaction amounts and counterparties, sanctions and PEP match
results, adverse-media findings. Tracing sends inputs and outputs to Langfuse.
That means **screening outcomes about identifiable people leave your
infrastructure**.

Even the seed data is shaped like real data, and the moment anyone points this
at a staging environment with production-derived records, it *is* real data. A
sanctions "potential match" against a named individual is among the most
sensitive categories you could transmit — it is an allegation about a person.

What the docs offer, and what each is actually worth:

| Control | Reality |
|---|---|
| **Data regions** — US (Oregon), EU (Ireland), JP (Tokyo) | Real and useful. EU region for UK/EU data residency |
| **Certifications** — SOC 2 Type II, ISO 27001, GDPR, HIPAA-ready region | Real, and probably what your risk function will ask for first |
| **Self-hosting** | The strongest control: data never leaves your infrastructure. Cost: Postgres + ClickHouse + Redis/Valkey + S3-compatible storage + web and worker containers. That is a real platform to operate, not a side-car |
| **SDK masking** (`mask_otel_spans`) | Client-side, so data is redacted *before* transmission — genuinely valuable. But: it cannot modify span names, ids, or resource attributes; it must be fast and deterministic; **an exception in your mask function drops the entire export batch**. It is a mitigation, not a guarantee, and a regex that misses one field silently exports that field |
| **Retention and deletion** | Useful for limiting blast radius; does not help with transmission |

**Recommendation:**

1. For the **seed fixtures only**, Langfuse Cloud in the EU region with masking
   is a defensible starting point — the data is synthetic. Confirm that with
   whoever owns data protection rather than assuming it.
2. For **anything derived from real customers**, self-host. Do not rely on
   masking as the primary control for special-category personal data.
3. Write the masking function early and test it as code, with fixtures for every
   PII-bearing field the API returns (`name`, `incorporation_or_dob`,
   `identifiers.dob`, beneficiary names, `matches[].listed_name`). Wrap it in a
   `try/except` that fails closed — because an exception drops the batch, a
   silent mask failure is a silent export failure.
4. Decide retention deliberately. Score history is what you want to keep long
   term; raw inputs and outputs are what you want to expire.

---

## 6. What I would actually do, in order

1. **Fix `PlanAdherenceMetric` first** (`--extra eval` in the backend image).
   One line, unblocks a requested metric, unrelated to Langfuse. Do not let a
   platform project delay it.
2. **Settle the PII question** with whoever owns data protection. Everything
   below depends on the answer, and discovering the answer late wastes the work.
3. **Phase 1, one metric, one scenario.** Prove the loop end to end: a run
   appears in Langfuse, a score is attached, a second run is comparable to the
   first. Timebox it — if the SDK fights you, that is data about the cost.
4. **Extend to all metrics that pass today** (13 of 14). Keep `run_all.py` as
   the portable, offline path; add `run_experiment.py` as the CI path. Never let
   the notebooks depend on Langfuse.
5. **Wire it into CI** on a schedule, not per-commit — each run costs real LLM
   spend on both the application and the judge.
6. **Only then consider Phase 2.** The trigger is a concrete question you cannot
   answer, e.g. "this metric regressed and I cannot tell which tool call caused
   it."
7. **Skip prompt management** for application prompts. Revisit only for judge
   rubrics.

### Cost note

Every experiment run costs twice: once for the application's own LLM calls, once
for the judge. Your suite is 14 notebooks, several of which judge per-chunk. The
`gpt-5.4-mini` judge default documented in `metric-notes.md` exists for exactly
this reason. Nightly is affordable; per-commit probably is not. Measure before
committing to a cadence.

---

## 7. Honest summary

**Genuinely valuable:** score history and run-over-run comparison; a shared
dashboard your team and auditors can look at; cost and latency visibility if you
do Phase 2; using DeepEval as the scorer and Langfuse as the system of record.

**Valuable but expensive:** application-side tracing (real backend change,
production dependency); self-hosting (real platform to operate); unified
test-to-application traces (needs trace-context propagation for a marginal gain
over correlating on `run_id`).

**Not valuable, or actively harmful here:** Langfuse prompt management for
application prompts; replacing DeepEval's agentic metrics with Langfuse's
generic judges; putting Langfuse inside the fourteen notebooks; expecting
Langfuse to unblock `PlanAdherenceMetric`.

**The main risk is not technical.** It is exporting AML screening outcomes about
named individuals to a third-party service because tracing was switched on
before anyone asked whether it should be. Settle that first.

---

## Sources

- [Langfuse docs](https://langfuse.com/docs)
- [Evaluation overview](https://langfuse.com/docs/evaluation/overview)
- [Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [Scores via API/SDK](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)
- [Python SDK instrumentation](https://langfuse.com/docs/observability/sdk/python/instrumentation)
- [Prompt management overview](https://langfuse.com/docs/prompt-management/overview)
- [Self-hosting](https://langfuse.com/self-hosting)
- [Security and compliance](https://langfuse.com/security)
- [Data masking](https://langfuse.com/docs/observability/features/masking)
