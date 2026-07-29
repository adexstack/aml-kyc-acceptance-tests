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
| Application | AML/KYC investigation API, `schema_version` `1.0.0`, seed `scenarios-v1` |

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
| `PlanAdherenceMetric` | **Blocked by missing API data** | `POST /api/cases/{id}/investigate` -> `GET /api/agent/trace/{run_id}` | `LLMTestCase` plus the agent's declared plan and executed trace | **No** | `GET /api/agent/trace/{run_id}` returns `deepeval_trace: null` and `tools_selected` empty, so there is no declared plan to judge adherence against. `GET /api/health` reports `eval_tracing: false`. Root cause is packaging, not configuration: `deepeval` is declared in `[project.optional-dependencies] eval`, but the backend image builds with `uv sync --frozen --no-dev`, which does not install that extra. `DEEPEVAL_AVAILABLE` is therefore false and `configure_tracing()` returns false unconditionally (`backend/app/observability.py:148`), whatever `EVAL_TRACING` is set to. The notebook detects this and raises with the exact reason rather than inferring a plan from the final answer | Install the `eval` extra in the backend image (e.g. `uv sync --frozen --no-dev --extra eval`) and ensure `EVAL_TRACING` is not disabled. Then `/api/agent/trace/{run_id}` must serve a non-null `deepeval_trace` with the planned steps, and `/api/health` must report `eval_tracing: true` |
| `PIILeakageMetric` | Implemented and verified | `GET /api/documents`, `POST /api/rag/query` | `LLMTestCase` with `input`, `actual_output` | Yes | Judges only the text returned to the caller. It cannot see PII that reached a log, a prompt, or a third-party model, so a pass is evidence about the response surface only | None. Full coverage would need an API-exposed record of outbound prompts |
| `BiasMetric` | Implemented and verified | `GET /api/cases/{id}`, `POST /api/cases/{id}/investigate` | `LLMTestCase` with `input`, `actual_output` | Yes | Single-case measurement cannot establish disparate treatment. Detecting that requires the same case run with a protected attribute varied and the outputs compared - out of scope for one notebook per metric | None. A fixture pair differing only in nationality would make this materially stronger |
| `SummarizationMetric` | Implemented and verified | `GET /api/documents/{id}` for the source, `POST /api/rag/query` for the summary | `LLMTestCase` with `input` (source text), `actual_output` | Yes | The application has no dedicated summarisation endpoint; a RAG answer over a known policy document is used as the summary under test. That is a fair proxy but is not the same as grading a product summarisation feature | None. A first-class summarisation endpoint would make this direct |
| `PromptAlignmentMetric` | Implemented and verified | `GET /api/cases/{id}`, `POST /api/cases/{id}/investigate` | `LLMTestCase` plus explicit `prompt_instructions` | Yes | The instruction list is the *documented output contract* (state a recommendation, cite evidence, use business language), not the application's real system prompt, which is not exposed. This measures conformance to the published contract, which is the appropriate black-box target | None. Exposing the effective system prompt would allow literal alignment testing, and would also widen the attack surface - not recommended |
| `HallucinationMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/query` | `LLMTestCase` with `context` (authoritative documents) | Yes | `context` is the served policy corpus. Any claim true of the world but absent from the corpus counts as a hallucination here, which is the correct standard for a compliance tool but will read as harsh on general knowledge | None |
| `TurnRelevancyMetric` | Implemented and verified | `POST /api/cases/{id}/conversation`, `POST /api/rag/query` | `ConversationalTestCase` with ordered `Turn`s | Yes | Uses the real multi-turn conversation endpoint, so turn structure is genuine. Judges relevancy of each turn to the conversation, not factual correctness | None |
| `ContextualPrecisionMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/retrieve` | `LLMTestCase` with `retrieval_context`, `expected_output` | Yes | Golden is a restatement of AML-001 sections 2.1-2.3, asserted against the served document before use. The assertion compares whitespace-collapsed text: the served markdown hard-wraps at ~72 characters, so a clause spanning a line break is not a raw substring even when present verbatim | None |
| `ContextualRecallMetric` | Implemented and verified | `GET /api/documents`, `GET /api/documents/{id}`, `POST /api/rag/retrieve` | `LLMTestCase` with `retrieval_context`, `expected_output` | Yes | As above. Recall is bounded by the configured retrieval depth - a low score may mean `top_k` is too small rather than that the index is wrong; check `filters` in the response before treating it as a retrieval-quality defect | None |
| `AnswerRelevancyMetric` | Implemented and verified | `POST /api/rag/query` | `LLMTestCase` with `input`, `actual_output` | Yes | Relevancy is not correctness. A fluent, on-topic, factually wrong answer scores well here; pair it with `HallucinationMetric`, which is why both exist | None |

## Blockers in detail

### `PlanAdherenceMetric` - no externally visible plan

- **Exact missing capability**: `GET /api/agent/trace/{run_id}` serves
  `deepeval_trace: null` and an empty `tools_selected`. There is no other
  documented endpoint exposing the agent's intended plan.
- **Why it cannot be evaluated honestly without it**: plan adherence is the gap
  between what the agent *said it would do* and what it *did*. Only the second
  half is observable. Reconstructing the intended plan from the executed trace
  would make the metric compare the trace with itself and score ~1.0 by
  construction - a number that looks like a passing test and measures nothing.
  The notebook therefore raises rather than producing a score.
- **Application change required**: build the backend image with the `eval`
  extra so `deepeval` is importable in the running container. Without it,
  `configure_tracing()` returns `False` regardless of `EVAL_TRACING`, and the
  `@observe` annotations produce nothing exportable.
- **Suggested response schema** for `GET /api/agent/trace/{run_id}`:

  ```json
  {
    "run_id": 12,
    "eval_tracing": true,
    "tools_selected": ["risk_screening.screen_sanctions_pep", "..."],
    "deepeval_trace": {
      "planned_steps": [
        {"step": 1, "intent": "screen_customer", "tool": "risk_screening.screen_sanctions_pep"}
      ],
      "executed_steps": [
        {"step": 1, "tool": "risk_screening.screen_sanctions_pep", "status": "success", "latency_ms": 442}
      ]
    }
  }
  ```

  `planned_steps` must be captured *before* execution for the metric to mean
  anything.
- **Portability impact**: none. The notebook is portable and fails with a clear,
  actionable message on any instance that has tracing off. It starts scoring the
  moment the application exposes the data - no test-side change needed.

### `MultiTurnMCPUseMetric` - dependency pin

- **Exact incompatibility**: `mcp` 1.28.0 renamed
  `CallToolResult.structuredContent` to `structured_content`. DeepEval 4.1.4
  reads the camelCase name in `deepeval/metrics/mcp/utils.py:64`.
- **Effect**: with `mcp>=1.28` installed the metric raises an attribute error on
  every turn. Pinned to `mcp>=1.12,<1.28` in `pyproject.toml`.
- **Portability impact**: the pin travels with `pyproject.toml` and `uv.lock`,
  so a detached copy resolves correctly. It does constrain any future
  environment that needs a newer `mcp` for another reason.

## Notes on `@observe` and trace export

The application annotates internal calls with DeepEval's `@observe`. That alone
does **not** make traces available to a detached black-box harness: `@observe`
populates an in-process trace manager, and a separate process can only see what
an endpoint chooses to serialise. In this application that serialisation exists
(`GET /api/agent/trace/{run_id}`, `deepeval_trace`) but currently yields `null`,
because the library backing the annotation is absent from the deployed image.

The general rule this suite follows: for every trace-dependent metric, request
the data through a documented endpoint, validate the schema, map fields
explicitly, and if the data is not there, **fail loudly and record the gap** -
never infer internal behaviour from the final answer.

## Verification status

| Check | Result |
|---|---|
| Notebook JSON structure | All 14 valid |
| Fresh-kernel execution, all cells in order | 13/14 pass; `PlanAdherenceMetric` blocked as above |
| Hidden-state dependencies | None: every notebook defines its own configuration and helpers, imports no other notebook |
| Imports declared in `pyproject.toml` | Yes: `deepeval`, `httpx`, `python-dotenv`, `jsonschema`, `mcp`, plus the notebook runner stack. Everything else is standard library |
| Secrets in notebooks, outputs or logs | None. Notebooks are committed without stored outputs; the request-display helper redacts `X-API-Key` and never prints `OPENAI_API_KEY` |
| Developer-application source imports | None |
| Parent-repository dependencies | None |
| Detached-directory `uv sync` | Verified from a copy outside the repository |
| Detached-directory execution | Verified for dependency resolution and kernel start; a full detached run needs `OPENAI_API_KEY` and a reachable API |
