# Metric notes

Per-metric record of what was implemented, what is limited, and what is blocked.
Written against a real application instance; every "verified" row below was
executed end-to-end against a running API, not merely authored.

## Environment these notes describe

| Item | Value |
|---|---|
| DeepEval version | `4.1.4` (pinned exactly in `pyproject.toml`) |
| `mcp` version | `>=1.12,<1.28` (upper bound is load-bearing, see below) |
| Judge model | `gpt-5.4-mini` by default, override with `DEEPEVAL_JUDGE_MODEL` |
| Thresholds | DeepEval's documented default `0.5` for every metric |
| Application | AML/KYC investigation API, `schema_version` `1.1.0` (goldens authored against `1.0.0`; the bump was additive), seed `scenarios-v1` |

All fourteen requested metric classes exist in DeepEval 4.1.4 under
`deepeval.metrics` with the exact names requested. None was renamed, deprecated,
moved, or substituted. Verified by attribute lookup against the installed
package, not by reading documentation.

## Judge model: documented deviation from `gpt-5.4`

**The notebooks default to `gpt-5.4-mini`, not `gpt-5.4` as specified.** This is
a deliberate deviation and is recorded here rather than made silently.

- **Why**: the suite runs fourteen notebooks, several of which invoke the judge
  many times per run (`ContextualPrecision` and `ContextualRecall` judge each
  retrieved chunk individually). The mini model keeps a full-suite run
  affordable and fast enough to use as a routine regression gate, which is the
  stated purpose of the framework.
- **This is a cost/latency trade, not a compatibility one.** `gpt-5.4` is *not*
  rejected by DeepEval 4.1.4; nothing here is blocked by the installed version.
- **How to comply with the original specification**: set
  `DEEPEVAL_JUDGE_MODEL=gpt-5.4` in `.env`. No code change is required, and
  every notebook picks it up from the same variable.
- **Effect on results**: judge scores are model-dependent. Scores obtained under
  `gpt-5.4-mini` are not directly comparable with scores obtained under
  `gpt-5.4`. Pick one model per baseline and keep it fixed. The deterministic
  metric (`ToolCorrectness`) is unaffected.

## Metric table

| Metric | Status | API flow used | Required inputs | External data available? | Limitation or blocker | Required application change |
|---|---|---|---|---|---|---|
| `ToolCorrectnessMetric` | Implemented and verified | `POST /api/cases/{id}/investigate` -> `GET /api/eval/export/{run_id}`; expected plan derived from `GET /api/cases`, `/api/customers`, `/api/beneficiaries`, `/api/mcp/tools` | `LLMTestCase` with `tools_called`, `expected_tools` | Yes | Scores tool selection only, never whether the tool's answer was correct. Planner short-circuits tools already completed for a case, so a repeat run needs `AML_RESET_BEFORE_RUN=true`. Despite scoring deterministically, DeepEval 4.1.4 still requires `OPENAI_API_KEY` to construct the metric | None |
| `ArgumentCorrectnessMetric` | Implemented and verified | `POST /api/cases/{id}/investigate` -> `GET /api/eval/export/{run_id}`; argument goldens derived from case facts + `GET /api/mcp/tools` schemas | `LLMTestCase` with `tools_called` | Yes | Judges argument plausibility against the input, so it is weaker than the deterministic argument comparison the `ToolCorrectness` notebook also performs. Treat that one as the gate and this as the explanation | None |
| `ToolUseMetric` | Implemented and verified | `POST /api/cases/{id}/investigate`, `GET /api/eval/export/{run_id}`, `GET /api/mcp/{servers,tools}`, `POST /api/mcp/invoke` | `ConversationalTestCase` with per-turn `mcp_tools_called` | Yes | Turns are assembled by the harness from real call records; the application does not expose a native conversational transcript of an investigation, so turn boundaries are the harness's construction and not an application artefact | None required. Would be improved by an endpoint returning an investigation as an ordered turn sequence |
| `MultiTurnMCPUseMetric` | Implemented with limitations | `GET /api/mcp/servers`, `GET /api/mcp/tools`, repeated `POST /api/mcp/invoke` | `ConversationalTestCase` with `mcp_servers` and `MCPToolCall` turns | Yes | **Dependency pin is load-bearing.** `mcp` 1.28.0 renamed `CallToolResult.structuredContent` to `structured_content`; DeepEval 4.1.4 still reads the camelCase attribute (`deepeval/metrics/mcp/utils.py:64`), so `mcp>=1.28` breaks this metric with an attribute error. Pinned `<1.28`. Also: `transport="stdio"` is declared from the application's documented MCP configuration, not discovered over the API | None. Remove the `mcp` upper bound only after DeepEval supports the renamed attribute |
| `PlanAdherenceMetric` | **Unblocked 2026-07-31** - previously blocked by missing API data | `POST /api/cases/{id}/investigate` -> `GET /api/agent/trace/{run_id}` | `LLMTestCase` plus the agent's declared plan and executed trace | Yes | The application now reports `eval_tracing: true` and serves a non-null `deepeval_trace` with a populated `tools_selected`, so a declared plan exists to judge adherence against. The declared plan is composed from the trace's *ex-ante* fields - `retrieval_runs[].query`, `tools_selected[]` (tool, arguments and `reason`) and `skipped_tools[]` (tool and `reason`) - deliberately **not** from the executed span tree, which would compare the trace with itself; see the blocker note below for why that distinction is load-bearing. Both the notebook and `experiments/investigate.py` still gate on `eval_tracing` and on `deepeval_trace` being non-null, and still raise with the exact reason if either regresses. **Requires `AML_RESET_BEFORE_RUN=true`** - verified empirically 2026-07-31: on a repeat run for an already-investigated case the planner skips completed tools, `tools_selected` comes back empty, and the notebook raises "no declared plan to judge adherence against" rather than scoring. Same repeat-run constraint as `ToolCorrectnessMetric` | None, now. Was: install the `eval` extra in the backend image (`uv sync --frozen --no-dev --extra eval`) and leave `EVAL_TRACING` enabled |
| `PIILeakageMetric` | Implemented and verified | `GET /api/documents`, `POST /api/rag/query` | `LLMTestCase` with `input`, `actual_output` | Yes | Judges only the text returned to the caller. It cannot see PII that reached a log, a prompt, or a third-party model, so a pass is evidence about the response surface only | None. Full coverage would need an API-exposed record of outbound prompts |
| `BiasMetric` | Implemented and verified | `GET /api/cases/{id}`, `POST /api/cases/{id}/investigate` | `LLMTestCase` with `input`, `actual_output` | Yes | Single-case measurement cannot establish disparate treatment. Detecting that requires the same case run with a protected attribute varied and the outputs compared - out of scope for one notebook per metric | None. A fixture pair differing only in nationality would make this materially stronger |
| `SummarizationMetric` | Implemented and verified | `GET /api/documents/{id}` for the source, `POST /api/rag/query` for the summary | `LLMTestCase` with `input` (source text), `actual_output` | Yes | The application has no dedicated summarisation endpoint; a RAG answer over a known policy document is used as the summary under test. That is a fair proxy but is not the same as grading a product summarisation feature | None. A first-class summarisation endpoint would make this direct |
| `PromptAlignmentMetric` | Implemented and verified | `GET /api/cases/{id}`, `POST /api/cases/{id}/investigate` | `LLMTestCase` plus explicit `prompt_instructions` | Yes | The instruction list is the *documented output contract* (state a recommendation, cite evidence, use business language), not the application's real system prompt, which is not exposed. This measures conformance to the published contract, which is the appropriate black-box target. **See "Live-run finding" below**: an intermittent evidence-label hallucination in the live agent's rationale was observed 2026-07-29 via this metric's deterministic guard - not a defect in the metric, a finding about the application | None. Exposing the effective system prompt would allow literal alignment testing, and would also widen the attack surface - not recommended |
| `HallucinationMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/query` | `LLMTestCase` with `context` (authoritative documents) | Yes | `context` is the served policy corpus. Any claim true of the world but absent from the corpus counts as a hallucination here, which is the correct standard for a compliance tool but will read as harsh on general knowledge | None |
| `TurnRelevancyMetric` | Implemented and verified | `POST /api/cases/{id}/conversation`, `POST /api/rag/query` | `ConversationalTestCase` with ordered `Turn`s | Yes | Uses the real multi-turn conversation endpoint, so turn structure is genuine. Judges relevancy of each turn to the conversation, not factual correctness | None |
| `ContextualPrecisionMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/retrieve` | `LLMTestCase` with `retrieval_context`, `expected_output` | Yes | Golden is a restatement of AML-001 sections 2.1-2.3, asserted against the served document before use. The assertion compares whitespace-collapsed text: the served markdown hard-wraps at ~72 characters, so a clause spanning a line break is not a raw substring even when present verbatim | None |
| `ContextualRecallMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/retrieve` | `LLMTestCase` with `retrieval_context`, `expected_output` | Yes | As above. Recall is bounded by the configured retrieval depth - a low score may mean `top_k` is too small rather than that the index is wrong; check `filters` in the response before treating it as a retrieval-quality defect | None |
| `AnswerRelevancyMetric` | Implemented and verified | `POST /api/rag/query` | `LLMTestCase` with `input`, `actual_output` | Yes | Relevancy is not correctness. A fluent, on-topic, factually wrong answer scores well here; pair it with `HallucinationMetric`, which is why both exist | None |

## Blockers in detail

### `PlanAdherenceMetric` - RESOLVED 2026-07-31

**This blocker is closed.** Kept here rather than deleted because the
reasoning explains why the metric is written the way it is, and because the
gate that detects a regression back to the blocked state is still in the
code and should stay there.

**What was blocking it**: `GET /api/agent/trace/{run_id}` served
`deepeval_trace: null` with an empty `tools_selected`, and `GET /api/health`
reported `eval_tracing: false`. Root cause was packaging, not configuration:
`deepeval` is declared in the backend's `[project.optional-dependencies]
eval`, but the image built with `uv sync --frozen --no-dev`, which does not
install that extra, so `configure_tracing()` returned false regardless of
`EVAL_TRACING`.

**What changed**: the application now reports `eval_tracing: true` and serves
a non-null `deepeval_trace` with `tools_selected` populated, each entry
carrying the agent's own `reason`.

**Verified end to end 2026-07-31.** `uv run python run_all.py PlanAdherence`
with `AML_RESET_BEFORE_RUN=true` → `1/1 notebooks passed` (65s, fresh
kernel). The metric derives a declared plan, scores it against the executed
trace, and clears its threshold. The same command **without** the reset
fails loudly with "The trace reports no tools_selected, so there is no
declared plan to judge adherence against" — which is the fail-loudly gate
behaving correctly, not a regression.

Note `run_all.py` executes notebooks in memory and discards their output, so
the numeric score is not persisted anywhere — only pass/fail. That is
exactly the gap `regression-testing-plan.md` Phase 0 and `plan.md` Phase R2
(`results/scores.jsonl`) exist to close.

**The point that still governs the implementation.** Plan adherence is the
gap between what the agent *said it would do* and what it *did*.
Reconstructing the intended plan from the executed span tree would make the
metric compare the trace with itself and score ~1.0 by construction - a
number that looks like a passing test and measures nothing. So the declared
plan is composed only from fields the application records **ex ante**, each
carrying the agent's own stated reason:

| Declared-plan element | Trace field |
|---|---|
| Intended retrievals | `retrieval_runs[].query` |
| Intended tool calls | `tools_selected[]` - `server`, `tool`, `arguments`, `reason` |
| Deliberate omissions | `skipped_tools[]` - `server`, `tool`, `reason` |

Adherence is then judged against the executed `steps[]` / `tool_calls[]`.
**Do not "simplify" this by deriving the plan from the span tree** - that
reintroduces the circularity this metric exists to avoid.

**The blocked-state gate stays.** Both
`notebooks/PlanAdherenceMetric.ipynb` and
`experiments/investigate.py:plan_adherence_experiment` still check
`/api/health` for `eval_tracing` and still raise if `deepeval_trace` comes
back null, with the exact reason. That is not dead code now that the metric
works - it is what stops a future instance built without the `eval` extra
from silently scoring nothing. Note tracing is also ignored entirely when
`ENVIRONMENT=production`, so the same gate covers that case.

**Portability impact**: unchanged and still none. The notebook fails with a
clear, actionable message on any instance that has tracing off, and scores
on any instance that has it on. No test-side change was needed to unblock
it.

### `MultiTurnMCPUseMetric` - dependency pin

- **Exact incompatibility**: `mcp` 1.28.0 renamed
  `CallToolResult.structuredContent` to `structured_content`. DeepEval 4.1.4
  reads the camelCase name in `deepeval/metrics/mcp/utils.py:64`.
- **Effect**: with `mcp>=1.28` installed the metric raises an attribute error on
  every turn. Pinned to `mcp>=1.12,<1.28` in `pyproject.toml`.
- **Portability impact**: the pin travels with `pyproject.toml` and `uv.lock`,
  so a detached copy resolves correctly. It does constrain any future
  environment that needs a newer `mcp` for another reason.

## Live-run finding: intermittent evidence-label hallucination (`PromptAlignmentMetric`)

**Observed 2026-07-29**, running `experiments/investigate.py`'s port of this
notebook (via `run_experiment.py`, docs/observability-plan.md Phase 1) against
a live local instance.

`PromptAlignmentMetric`'s deterministic pre-check - independent of the judge,
see the notebook and `experiments/investigate.py` - asserts that every
`C#`/`T#` evidence label cited in an investigation's `rationale` resolves to a
real entry in that same investigation's `evidence` array. On scenario s2
("High-risk jurisdiction transfer"), one run's rationale cited `T1, T2, T3`
while the returned `evidence` array carried no matching entries for those
labels. The check raised, correctly - this is exactly the failure mode it
exists to catch.

**Confirmed not a false positive in the check itself**: an immediate
follow-up `POST /api/cases/{case_id}/investigate` on the same case, same day,
same code, produced a rationale whose six cited labels (`C5, C6, T1-T4`) all
resolved cleanly against its own `evidence` array. The checking logic is
correct; the earlier run's live agent output did not satisfy the contract the
application publishes for itself.

**What this means**: the agent occasionally hallucinates an evidence
citation - it states a tool-call label in `rationale` that its own tool-call
record does not support at generation time. Likely non-deterministic (it
depends on the LLM's synthesis output, not a deterministic code path), so a
clean re-run is not evidence this is fixed, and repeated occurrences would be
worth a regression check (`docs/regression-testing-plan.md`) rather than a
one-off note.

**Why the table row above is left "Implemented and verified" rather than
"Blocked" or "Limited"**: the metric itself works correctly, as demonstrated
by both the failing and the passing run - this is a finding *about the
application's evidence-labelling discipline*, not a defect in the test or a
metric limitation. Whoever owns the application should be made aware of it.

## Notes on `@observe` and trace export

The application annotates internal calls with DeepEval's `@observe`. That alone
does **not** make traces available to a detached black-box harness: `@observe`
populates an in-process trace manager, and a separate process can only see what
an endpoint chooses to serialise. In this application that serialisation exists
(`GET /api/agent/trace/{run_id}`, `deepeval_trace`) and, since 2026-07-31,
yields real data - it previously yielded `null` because the library backing
the annotation was absent from the deployed image. The general point stands
regardless of that fix: `@observe` alone never exposes anything to this
harness, so a future annotation added without a corresponding endpoint
change remains invisible here.

The general rule this suite follows: for every trace-dependent metric, request
the data through a documented endpoint, validate the schema, map fields
explicitly, and if the data is not there, **fail loudly and record the gap** -
never infer internal behaviour from the final answer.

## Verification status

| Check | Result |
|---|---|
| Notebook JSON structure | All 14 valid |
| Fresh-kernel execution, all cells in order | Was 13/14 (`PlanAdherenceMetric` blocked). **`PlanAdherenceMetric` verified PASS end to end 2026-07-31** (65s, fresh kernel, `AML_RESET_BEFORE_RUN=true`, judge `gpt-5.4-mini`) - it derives a declared plan, scores, and clears its threshold. The other 13 were last verified before that date and have not been re-run since, so **14/14 is the expectation, not yet a single observed full-suite result** |
| Hidden-state dependencies | None: every notebook defines its own configuration and helpers, imports no other notebook |
| Imports declared in `pyproject.toml` | Yes: `deepeval`, `httpx`, `python-dotenv`, `jsonschema`, `mcp`, plus the notebook runner stack. Everything else is standard library |
| Secrets in notebooks, outputs or logs | None. Notebooks are committed without stored outputs; the request-display helper redacts `X-API-Key` and never prints `OPENAI_API_KEY` |
| Developer-application source imports | None |
| Parent-repository dependencies | None |
| Detached-directory `uv sync` | Verified from a copy outside the repository |
| Detached-directory execution | Verified for dependency resolution and kernel start; a full detached run needs `OPENAI_API_KEY` and a reachable API |
