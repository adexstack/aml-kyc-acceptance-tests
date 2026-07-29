"""Retrieval-only metrics: task is POST /api/rag/retrieve, no LLM call.

ContextualPrecisionMetric, ContextualRecallMetric - ported from
notebooks/ContextualPrecisionMetric.ipynb and
notebooks/ContextualRecallMetric.ipynb, which deliberately share the same
request (query, document_type, top_k) and the same hand-authored golden, so
precision and recall are read together. See either notebook for why the
golden is verified against the served AML-001 text before use, and why
`actual_output` is set to the golden (unused by either metric, kept only so
the test case is well-formed).
"""

from __future__ import annotations

import re

from experiments.common import JUDGE_MODEL, api, item_input, load_scenarios, maybe_reset

_QUERY = (
    "What indicators identify structuring, and what cash transaction thresholds "
    "require review under the transaction monitoring policy?"
)

_EXPECTED_OUTPUT = (
    "Under AML-001, any single cash transaction of GBP 10,000 or more must be recorded "
    "and reviewed before processing, and multiple cash transactions by or on behalf of "
    "the same customer totalling GBP 25,000 or more within any rolling 30-day period "
    "must be aggregated and reviewed as a pattern regardless of individual transaction "
    "size. A pattern of cash deposits individually below GBP 10,000 that appears "
    "designed to avoid the single-transaction threshold is structuring and must be "
    "escalated for investigation. The stated indicators are deposits within 10% below "
    "the threshold, deposits on consecutive days, and deposits split across multiple "
    "branches."
)

_REQUIRED_CLAUSES = [
    "GBP 10,000 or more must be recorded and reviewed before processing",
    "GBP 25,000 or more within any rolling 30-day period must be aggregated",
    "deposits within 10% below the threshold",
    "deposits on consecutive days",
    "deposits split across multiple branches",
]


def _verified_golden() -> str:
    """Confirm every clause in the golden is present in the served AML-001
    text before using it, so the golden cannot silently drift from the
    corpus (same whitespace-collapsing comparison as the source notebooks -
    the served markdown hard-wraps, so a clause spanning a line break is
    not a raw substring even when present verbatim)."""
    policy_docs = api("GET", "/api/documents", params={"type": "policy"})
    aml001 = next(d for d in policy_docs if "AML-001" in (d.get("source") or ""))
    aml001_content = api("GET", f"/api/documents/{aml001['document_id']}")["content"]
    flat = re.sub(r"\s+", " ", aml001_content)
    missing = [c for c in _REQUIRED_CLAUSES if re.sub(r"\s+", " ", c) not in flat]
    if missing:
        raise RuntimeError(
            "The golden cites clauses not present in the document the API serves: "
            f"{missing}. Update the golden from the current document text rather than "
            "weakening the assertion."
        )
    return _EXPECTED_OUTPUT


def _build(name: str, metric_evaluator) -> dict:
    maybe_reset()
    load_scenarios()  # seed-version guard
    golden = _verified_golden()

    data = [{
        "input": {"query": _QUERY},
        "expected_output": golden,
        "metadata": {"document_type": "policy", "top_k": 10},
    }]

    def task(*, item, **_kwargs):
        i = item_input(item)
        retrieval = api("POST", "/api/rag/retrieve",
                        json_body={"query": i["query"], "document_type": "policy",
                                   "top_k": 10})
        return {
            "query": retrieval["query"],
            "retrieval_context": [chunk["text"] for chunk in retrieval["chunks"]],
        }

    return {"name": name, "data": data, "task": task, "evaluators": [metric_evaluator]}


# ---------------------------------------------------------------------------
# ContextualPrecisionMetric - notebooks/ContextualPrecisionMetric.ipynb
# ---------------------------------------------------------------------------

def contextual_precision_experiment() -> dict:
    def contextual_precision(*, output, expected_output, **_kwargs):
        from deepeval.metrics import ContextualPrecisionMetric
        from deepeval.test_case import LLMTestCase
        from langfuse import Evaluation

        metric = ContextualPrecisionMetric(threshold=0.5, model=JUDGE_MODEL,
                                            include_reason=True, async_mode=False)
        tc = LLMTestCase(input=output["query"], actual_output=expected_output,
                          expected_output=expected_output,
                          retrieval_context=output["retrieval_context"])
        metric.measure(tc)
        return Evaluation(name="contextual_precision", value=metric.score,
                           comment=metric.reason)

    return _build("aml-acceptance-contextual-precision", contextual_precision)


# ---------------------------------------------------------------------------
# ContextualRecallMetric - notebooks/ContextualRecallMetric.ipynb
# ---------------------------------------------------------------------------

def contextual_recall_experiment() -> dict:
    def contextual_recall(*, output, expected_output, **_kwargs):
        from deepeval.metrics import ContextualRecallMetric
        from deepeval.test_case import LLMTestCase
        from langfuse import Evaluation

        metric = ContextualRecallMetric(threshold=0.5, model=JUDGE_MODEL,
                                         include_reason=True, async_mode=False)
        tc = LLMTestCase(input=output["query"], actual_output=expected_output,
                          expected_output=expected_output,
                          retrieval_context=output["retrieval_context"])
        metric.measure(tc)
        return Evaluation(name="contextual_recall", value=metric.score,
                           comment=metric.reason)

    return _build("aml-acceptance-contextual-recall", contextual_recall)


EXPERIMENTS = [
    contextual_precision_experiment,
    contextual_recall_experiment,
]
