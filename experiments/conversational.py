"""Conversational (multi-turn) metrics: ToolUse, MultiTurnMCPUse,
TurnRelevancy.

Ported from notebooks/{ToolUseMetric,MultiTurnMCPUseMetric,
TurnRelevancyMetric}.ipynb - read those for the full narrative. Two of the
three (`ToolUseMetric`, `MultiTurnMCPUseMetric`) score a conversation
**assembled by the harness** from real single-turn API calls, because the
application has no multi-turn tool-calling endpoint - see either notebook
for exactly which parts are real (every tool name, argument, result,
status, execution order) and which are imposed (the framing as a
conversation). `TurnRelevancyMetric` is the one metric whose multi-turn
structure is genuinely the application's own (`POST /api/rag/query`'s
case-scoped conversation memory).

Naming convention (see langfuse_mask.py and experiments/investigate.py):
plain dicts handed to Langfuse use `tool`/`server`, never `name` or
`namespaced_name` (both contain the substring the masker matches on).
DeepEval's own typed objects, which do require those exact field/attribute
names, are constructed locally inside evaluators from real, already
in-memory values.
"""

from __future__ import annotations

from experiments.common import (JUDGE_MODEL, api, app_latency, item_input, load_scenarios,
                                maybe_reset, record_app_run, run_context)


# ---------------------------------------------------------------------------
# Shared assembly for ToolUseMetric and MultiTurnMCPUseMetric: one
# investigation plus two direct POST /api/mcp/invoke probes on the same
# case, laid out as a conversation. See notebooks/ToolUseMetric.ipynb
# ("How the conversation is assembled, and what that costs") for why the
# shape matters: MultiTurnMCPUseMetric skips any unit interaction of two
# turns or fewer, so a flat user/assistant alternation scores a false 0.0.
# ---------------------------------------------------------------------------

def _direct_probes(case_id: str, beneficiary_country: str | None) -> list[dict]:
    probes = [
        {
            "user_intent": f"Is {beneficiary_country} on the internal high-risk "
                           f"jurisdiction list?",
            "server": "risk_screening", "tool_name": "check_high_risk_country",
            "arguments": {"country_code": beneficiary_country},
        },
        {
            "user_intent": "Find the sanctions screening extract filed on this case.",
            "server": "evidence", "tool_name": "search_case_documents",
            "arguments": {"case_id": case_id, "query": "sanctions screening extract"},
        },
    ]
    return probes


def _assemble_conversation(case_id: str) -> dict:
    """Real API calls: one investigation, then two direct MCP invocations.
    Returns a plain, JSON-safe structure - no DeepEval objects yet."""
    case = api("GET", f"/api/cases/{case_id}")
    beneficiary_country = None
    if case.get("transaction") and case["transaction"].get("beneficiary_id"):
        beneficiary_country = api(
            "GET", f"/api/beneficiaries/{case['transaction']['beneficiary_id']}")["country"]

    investigation = api("POST", f"/api/cases/{case_id}/investigate", expect_status=201)
    run_id = investigation["run_id"]
    export = api("GET", f"/api/eval/export/{run_id}", role="eval_reader")

    failed = [c for c in investigation["tools_called"] if c["status"] != "success"]
    if failed and len(failed) == len(investigation["tools_called"]):
        raise RuntimeError(
            "Every tool call in this run failed, so there is no successful tool use to "
            "score. Check GET /api/mcp/servers - a server that is down reports status "
            "'unavailable'."
        )

    investigation_calls = [
        {"tool": f"{c['server']}.{c['tool']}", "input_parameters": c["input"],
         "output": c["output"], "status": c["status"]}
        for c in investigation["tools_called"]
    ]

    probes = _direct_probes(case_id, beneficiary_country)
    direct_calls = []
    for probe in probes:
        response_body = api("POST", "/api/mcp/invoke", role="test_operator", json_body={
            "server": probe["server"], "tool": probe["tool_name"],
            "arguments": probe["arguments"],
        })
        direct_calls.append({
            "user_intent": probe["user_intent"],
            "tool": response_body["namespaced_name"],
            "input_parameters": probe["arguments"],
            "output": response_body["output"],
            "status": response_body["status"],
        })

    return {
        "user_input": export["input"],
        "investigation_calls": investigation_calls,
        "final_answer": export["actual_output"],
        "direct_calls": direct_calls,
        # The investigation is the application run this conversation is
        # about; the two direct MCP probes are the harness's own additions.
        # So the fingerprint and app_latency_ms describe the investigation,
        # not the assembled transcript's total wall-clock.
        "app_run": record_app_run(investigation),
    }


def _tool_catalogue() -> list[dict]:
    mcp_tools = api("GET", "/api/mcp/tools", role="eval_reader")
    return [{"tool": tool["namespaced_name"], "description": tool["description"]}
            for server in mcp_tools for tool in server["tools"]]


def _server_declarations() -> list[dict]:
    mcp_tools = api("GET", "/api/mcp/tools", role="eval_reader")
    return [
        {
            "server": server["server"],
            "transport": "stdio",  # as declared in the application's MCP config
            "tools": [{"tool": t["namespaced_name"], "description": t["description"],
                       "input_schema": t["input_schema"]} for t in server["tools"]],
        }
        for server in mcp_tools
    ]


def _as_call_tool_result(output, status):
    from mcp.types import CallToolResult, TextContent
    import json as _json
    return CallToolResult(
        content=[TextContent(type="text", text=_json.dumps(output))],
        structuredContent={"result": output},
        isError=status != "success",
    )


def _turns_from_conversation(conversation: dict):
    """Reconstruct DeepEval Turn/ToolCall/MCPToolCall objects from the plain
    structure _assemble_conversation() produced. Called inside evaluators,
    from real in-memory data - never from anything that passed through
    mask()."""
    import json as _json

    from deepeval.test_case import Turn, ToolCall
    from deepeval.test_case.mcp import MCPToolCall

    turns = [Turn(role="user", content=conversation["user_input"])]
    for call in conversation["investigation_calls"]:
        turns.append(Turn(
            role="assistant",
            content=f"Calling MCP tool {call['tool']} with {_json.dumps(call['input_parameters'])}.",
            tools_called=[ToolCall(name=call["tool"], input_parameters=call["input_parameters"],
                                    output=call["output"])],
            mcp_tools_called=[MCPToolCall(
                name=call["tool"], args=call["input_parameters"],
                result=_as_call_tool_result(call["output"], call["status"]))],
        ))
    turns.append(Turn(role="assistant", content=conversation["final_answer"]))

    for call in conversation["direct_calls"]:
        turns.append(Turn(role="user", content=call["user_intent"]))
        turns.append(Turn(
            role="assistant",
            content=f"Calling MCP tool {call['tool']} with {_json.dumps(call['input_parameters'])}.",
            tools_called=[ToolCall(name=call["tool"], input_parameters=call["input_parameters"],
                                    output=call["output"])],
            mcp_tools_called=[MCPToolCall(
                name=call["tool"], args=call["input_parameters"],
                result=_as_call_tool_result(call["output"], call["status"]))],
        ))
        turns.append(Turn(role="assistant", content=_json.dumps(call["output"])[:1200]))

    return turns


# ---------------------------------------------------------------------------
# ToolUseMetric - notebooks/ToolUseMetric.ipynb
# ---------------------------------------------------------------------------

def tool_use_experiment() -> dict:
    maybe_reset()
    s = load_scenarios()["s3"]
    case_id = s["case_id"]
    catalogue = _tool_catalogue()

    data = [{
        "input": {"case_id": case_id},
        "expected_output": catalogue,  # the whole catalogue, incl. tools never used
        "metadata": {"scenario_id": "s3", "title": s["title"],
                     "seed_version": s["seed_version"]},
    }]

    def task(*, item, **_kwargs):
        i = item_input(item)
        return _assemble_conversation(i["case_id"])

    def tool_use(*, output, expected_output, **_kwargs):
        from deepeval.metrics import ToolUseMetric
        from deepeval.test_case import ConversationalTestCase, ToolCall
        from langfuse import Evaluation

        available_tools = [ToolCall(name=t["tool"], description=t["description"])
                            for t in expected_output]
        tc = ConversationalTestCase(turns=_turns_from_conversation(output))
        metric = ToolUseMetric(available_tools=available_tools, threshold=0.5,
                               model=JUDGE_MODEL, include_reason=True, async_mode=False)
        metric.measure(tc)
        return Evaluation(name="tool_use", value=metric.score, comment=metric.reason,
                          metadata=run_context(output.get("app_run"), _kwargs.get("metadata")))

    return {"name": "aml-acceptance-tool-use", "data": data, "task": task,
            "evaluators": [tool_use, app_latency]}


# ---------------------------------------------------------------------------
# MultiTurnMCPUseMetric - notebooks/MultiTurnMCPUseMetric.ipynb
#
# Combined score is min(mean(primitive), mean(argument)); both sub-scores
# are reported as separate Evaluations since the notebook treats the split
# as the metric's real diagnostic value, not the combined number.
# ---------------------------------------------------------------------------

def multi_turn_mcp_use_experiment() -> dict:
    maybe_reset()
    s = load_scenarios()["s3"]
    case_id = s["case_id"]
    servers = _server_declarations()

    data = [{
        "input": {"case_id": case_id},
        "expected_output": servers,
        "metadata": {"scenario_id": "s3", "title": s["title"],
                     "seed_version": s["seed_version"]},
    }]

    def task(*, item, **_kwargs):
        i = item_input(item)
        conversation = _assemble_conversation(i["case_id"])

        # Guard: skip any unit interaction of two turns or fewer - the
        # metric would extract no tasks and return 0.0 with an empty
        # reason, a false failure that looks exactly like a real one.
        turns = _turns_from_conversation(conversation)
        units, current, seen_user = [], [], False
        for turn in turns:
            if current and current[-1].role == "assistant" and turn.role == "user" and seen_user:
                units.append(current)
                current, seen_user = [turn], True
                continue
            current.append(turn)
            seen_user = seen_user or turn.role == "user"
        if current and len(current) > 1 and current[-1].role == "assistant" and seen_user:
            units.append(current)
        if not any(len(u) > 2 for u in units):
            raise RuntimeError(
                "No unit interaction has more than two turns, so MultiTurnMCPUseMetric "
                "would extract no tasks and return 0.0 with an empty reason - a false "
                "failure."
            )

        return conversation

    def multi_turn_mcp_use(*, output, expected_output, **_kwargs):
        from deepeval.metrics import MultiTurnMCPUseMetric
        from deepeval.test_case import ConversationalTestCase
        from deepeval.test_case.mcp import MCPServer
        from mcp.types import Tool as MCPTool
        from langfuse import Evaluation

        mcp_servers = [
            MCPServer(
                server_name=server["server"], transport=server["transport"],
                available_tools=[MCPTool(name=t["tool"], description=t["description"],
                                         inputSchema=t["input_schema"])
                                 for t in server["tools"]],
            )
            for server in expected_output
        ]
        tc = ConversationalTestCase(turns=_turns_from_conversation(output),
                                    mcp_servers=mcp_servers)
        metric = MultiTurnMCPUseMetric(threshold=0.5, model=JUDGE_MODEL,
                                       include_reason=True, async_mode=False)
        metric.measure(tc)
        return Evaluation(name="multi_turn_mcp_use", value=metric.score,
                          comment=metric.reason,
                          metadata=run_context(output.get("app_run"), _kwargs.get("metadata")))

    def primitive_accuracy(*, output, expected_output, **_kwargs):
        # Re-measures to read the sub-score lists. Cheap relative to the
        # judge cost already paid by multi_turn_mcp_use() - DeepEval does
        # not expose sub-scores without a full measure() call.
        from deepeval.metrics import MultiTurnMCPUseMetric
        from deepeval.test_case import ConversationalTestCase
        from deepeval.test_case.mcp import MCPServer
        from mcp.types import Tool as MCPTool
        from langfuse import Evaluation

        mcp_servers = [
            MCPServer(server_name=s["server"], transport=s["transport"],
                     available_tools=[MCPTool(name=t["tool"], description=t["description"],
                                              inputSchema=t["input_schema"])
                                      for t in s["tools"]])
            for s in expected_output
        ]
        tc = ConversationalTestCase(turns=_turns_from_conversation(output),
                                    mcp_servers=mcp_servers)
        metric = MultiTurnMCPUseMetric(threshold=0.5, model=JUDGE_MODEL,
                                       include_reason=True, async_mode=False)
        metric.measure(tc)
        tool_scores = [s for s, _ in (getattr(metric, "tools_scores_reasons_list", None) or [])]
        arg_scores = [s for s, _ in (getattr(metric, "args_scores_reasons_list", None) or [])]
        mean = lambda xs: sum(xs) / len(xs) if xs else None
        context = run_context(output.get("app_run"), _kwargs.get("metadata"))
        return [
            Evaluation(name="mcp_primitive_accuracy", value=mean(tool_scores),
                       metadata=context),
            Evaluation(name="mcp_argument_accuracy", value=mean(arg_scores),
                       metadata=context),
        ]

    return {"name": "aml-acceptance-multi-turn-mcp-use", "data": data, "task": task,
            "evaluators": [multi_turn_mcp_use, primitive_accuracy, app_latency]}


# ---------------------------------------------------------------------------
# TurnRelevancyMetric - notebooks/TurnRelevancyMetric.ipynb
#
# The one metric in this module whose multi-turn structure is genuinely the
# application's own. The three questions are deliberately interdependent
# ("that list", "this case") - a conversation of independent questions
# would score identically with memory switched off and test nothing.
# ---------------------------------------------------------------------------

_TURN_RELEVANCY_QUESTIONS = [
    "What does policy require before releasing an outbound wire to a high-risk jurisdiction?",
    "Which jurisdictions are on that list?",
    "Does the transaction on this case fall under those requirements?",
]


def turn_relevancy_experiment() -> dict:
    maybe_reset()
    s = load_scenarios()["s2"]
    case_id = s["case_id"]

    data = [{
        "input": {"case_id": case_id},
        "expected_output": None,  # reference-free metric
        "metadata": {"scenario_id": "s2", "title": s["title"],
                     "seed_version": s["seed_version"]},
    }]

    def task(*, item, **_kwargs):
        i = item_input(item)
        cid = i["case_id"]

        # Clear the thread so the transcript starts from a known state.
        # DELETE (not dev/reset) is scoped to this one case.
        api("DELETE", f"/api/cases/{cid}/conversation")

        responses = [
            api("POST", "/api/rag/query", json_body={"question": q, "case_id": cid,
                                                       "top_k": 6})
            for q in _TURN_RELEVANCY_QUESTIONS
        ]

        conversation_ids = {r["conversation_id"] for r in responses}
        if len(conversation_ids) != 1:
            raise RuntimeError(
                f"Expected all turns on one conversation thread, got {conversation_ids}. "
                f"Conversation memory is case-scoped; a differing id means the turns "
                f"were not threaded and there is no multi-turn behaviour to score."
            )
        if responses[1]["history_turns_used"] == 0:
            raise RuntimeError(
                "Turn 2 reports history_turns_used = 0, so no prior turn was fed back "
                "in. The conversation is not actually multi-turn."
            )

        conversation = api("GET", f"/api/cases/{cid}/conversation")
        if conversation["turn_count"] < len(_TURN_RELEVANCY_QUESTIONS):
            raise RuntimeError(
                f"The application recorded {conversation['turn_count']} turns but "
                f"{len(_TURN_RELEVANCY_QUESTIONS)} questions were asked. Scoring an "
                f"incomplete transcript would misattribute a persistence bug to relevance."
            )

        turns = []
        for recorded in sorted(conversation["turns"], key=lambda t: t["turn_index"]):
            turns.append({"role": "user", "content": recorded["question"]})
            turns.append({"role": "assistant", "content": recorded["answer"]})
        # Fingerprint from the first turn - model and prompt_version are
        # the same across the thread. No latency score is emitted for this
        # metric (see the evaluators list below).
        return {"turns": turns, "app_run": record_app_run(responses[0])}

    def turn_relevancy(*, output, **_kwargs):
        from deepeval.metrics import TurnRelevancyMetric
        from deepeval.test_case import ConversationalTestCase, Turn
        from langfuse import Evaluation

        turns = [Turn(role=t["role"], content=t["content"]) for t in output["turns"]]
        tc = ConversationalTestCase(turns=turns)
        metric = TurnRelevancyMetric(threshold=0.5, model=JUDGE_MODEL, include_reason=True,
                                     async_mode=False, window_size=10)
        metric.measure(tc)
        return Evaluation(name="turn_relevancy", value=metric.score, comment=metric.reason,
                          metadata=run_context(output.get("app_run"), _kwargs.get("metadata")))

    # No `app_latency` here, deliberately: this task makes three application
    # calls and none of them is "the" run this metric is about. Emitting one
    # turn's latency under the same score name as every other metric's
    # whole-run latency, or summing three turns into one number, would both
    # be fabricated comparables (docs/performance-latency-plan.md §5).
    return {"name": "aml-acceptance-turn-relevancy", "data": data, "task": task,
            "evaluators": [turn_relevancy]}


EXPERIMENTS = [
    tool_use_experiment,
    multi_turn_mcp_use_experiment,
    turn_relevancy_experiment,
]
