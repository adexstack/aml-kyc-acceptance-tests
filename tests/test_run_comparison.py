"""Unit tests for the run-identity fingerprint and the comparability guard.

Run with: uv sync --extra dev --extra langfuse && \
          uv run pytest tests/test_run_comparison.py -v

Covers the two decisions that make a stored score trustworthy (plan.md §0):

  * a value that cannot change when the code changes is NOT a build
    identity, and must be recorded as "unavailable" rather than as a
    fingerprint that looks like it works;
  * two runs whose MEASUREMENT axes differ are not comparable, and the
    default must be refusal rather than a warning that scrolls past.

No network: every test substitutes the cached /api/health payload.
"""

from __future__ import annotations

import pytest

import compare_runs
from experiments import common


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Serve a fixed health payload instead of calling the application."""
    monkeypatch.setattr(common, "_health_cache",
                        {"status": "ok", "schema_version": "1.1.0", "eval_tracing": True,
                         "build_version": "0.1.0+3f68be6"})
    yield


def _set_health(monkeypatch, **fields):
    payload = {"status": "ok", "schema_version": "1.1.0", "eval_tracing": True}
    payload.update(fields)
    monkeypatch.setattr(common, "_health_cache", payload)


# ---------------------------------------------------------------------------
# app_build: a constant is not a fingerprint
# ---------------------------------------------------------------------------

def test_real_build_version_is_recorded_verbatim():
    assert common.app_build() == "0.1.0+3f68be6"


def test_unknown_fallback_is_reported_as_unavailable(monkeypatch):
    # The application's documented fallback when the image carries no git
    # metadata. It never changes, so recording it would be a signal that
    # looks like it works and does not.
    _set_health(monkeypatch, build_version="0.1.0+unknown")
    assert common.app_build() == common.UNAVAILABLE


def test_absent_build_version_is_reported_as_unavailable(monkeypatch):
    _set_health(monkeypatch)
    assert common.app_build() == common.UNAVAILABLE


def test_dirty_build_is_kept_because_it_still_identifies_a_tree(monkeypatch):
    _set_health(monkeypatch, build_version="0.1.0+3f68be6.dirty")
    assert common.app_build() == "0.1.0+3f68be6.dirty"


# ---------------------------------------------------------------------------
# Contract version: minor-compatible, not exact-match
# ---------------------------------------------------------------------------

def test_additive_minor_bump_is_accepted_silently(monkeypatch, capsys):
    _set_health(monkeypatch, schema_version="1.1.0")
    assert common.check_schema_version() == "1.1.0"
    assert capsys.readouterr().out == ""


def test_major_change_refuses(monkeypatch):
    _set_health(monkeypatch, schema_version="2.0.0")
    with pytest.raises(RuntimeError, match="MAJOR change"):
        common.check_schema_version()


def test_older_application_warns(monkeypatch, capsys):
    _set_health(monkeypatch, schema_version="1.0.0")
    monkeypatch.setattr(common, "EXPECTED_SCHEMA_VERSION", "1.2.0")
    common.check_schema_version()
    assert "older than" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The fingerprint itself
# ---------------------------------------------------------------------------

def test_every_axis_is_always_present_even_without_an_app_run():
    context = common.run_context()
    for axis in common.MEASUREMENT_AXES + common.APPLICATION_AXES:
        assert axis in context, f"{axis} missing - a key must never be omitted"
        assert isinstance(context[axis], str), f"{axis} must be a flat string"


def test_retrieval_run_is_not_applicable_rather_than_unavailable():
    key = common.record_app_run({"retrieval_run_id": 12, "latency_ms": 40})
    context = common.run_context(key)
    # The retrieval endpoint runs no LLM: it HAS no model. That is a
    # different statement from "should exist but could not be read".
    assert context["app_model"] == common.NOT_APPLICABLE
    assert context["app_prompt_version"] == common.NOT_APPLICABLE
    assert context["app_temperature"] == common.NOT_APPLICABLE
    assert context["app_run_id"] == "12"


def test_agent_run_fingerprint_comes_from_the_response_without_extra_calls(monkeypatch):
    monkeypatch.setattr(common, "trace", lambda run_id: {"model_configuration":
                                                          {"temperature": 0.0}})
    key = common.record_app_run({"run_id": 7, "model": "openai/gpt-4o-mini",
                                 "prompt_version": "investigation_v1", "latency_ms": 7594})
    context = common.run_context(key)
    assert context["app_model"] == "openai/gpt-4o-mini"
    assert context["app_prompt_version"] == "investigation_v1"
    assert context["app_temperature"] == "0.0"


def test_scenario_is_recorded_for_drill_down_but_is_not_an_axis():
    context = common.run_context(item_metadata={"scenario_id": "s3", "title": "..."})
    assert context["item_scenario"] == "s3"
    # It identifies the item, not the run: comparing two runs must not refuse
    # because they scored different scenarios.
    assert "item_scenario" not in common.MEASUREMENT_AXES + common.APPLICATION_AXES


def test_caseless_item_records_not_applicable_for_scenario():
    # The summarization and retrieval experiments query a document, not a case.
    assert common.run_context(item_metadata={"document_type": "policy"})["item_scenario"] \
        == common.NOT_APPLICABLE


def test_latency_scores_carry_the_scenario_too(monkeypatch):
    monkeypatch.setattr(common, "trace", lambda run_id: {"steps": []})
    key = common.record_app_run({"run_id": 9, "model": "m", "prompt_version": "p",
                                 "latency_ms": 1234})
    evaluations = common.app_latency(output={"app_run": key},
                                     metadata={"scenario_id": "s5"})
    assert [e.name for e in evaluations] == ["app_latency_ms"]
    assert evaluations[0].metadata["item_scenario"] == "s5"


def test_a_response_with_no_run_identifier_fails_loudly():
    with pytest.raises(RuntimeError, match="no application run"):
        common.record_app_run({"answer": "..."})


# ---------------------------------------------------------------------------
# compare_runs: refuse before you compare
# ---------------------------------------------------------------------------

def _score(label, name, value, timestamp, **overrides):
    metadata = {"run_label": label, "judge_model": "gpt-5.4-mini",
                "seed_version": "scenarios-v1", "schema_version": "1.1.0",
                "reset_before_run": "true", "harness_sha": "abc1234",
                "scenario_count": "8", "app_model": "openai/gpt-4o-mini",
                "app_prompt_version": "investigation_v1", "app_temperature": "0.0",
                "app_build": "0.1.0+3f68be6", "app_run_id": "1"}
    metadata.update(overrides)
    return {"id": f"{label}-{name}-{timestamp}", "name": name, "value": value,
            "timestamp": timestamp, "comment": None, "metadata": metadata}


def _grouped(scores):
    values, axes, first_seen = compare_runs.group_by_label(scores)
    return values, axes, sorted(values, key=lambda label: first_seen[label])


def test_matched_runs_are_comparable():
    _, axes, labels = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00"),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00"),
    ])
    assert compare_runs.check_comparable(axes, labels) == []


@pytest.mark.parametrize("axis,changed", [
    ("judge_model", "gpt-5.4"),
    ("seed_version", "scenarios-v2"),
    ("schema_version", "2.0.0"),
    ("reset_before_run", "false"),
    ("harness_sha", "def5678"),
    ("scenario_count", "7"),
])
def test_any_measurement_change_blocks_the_comparison(axis, changed):
    _, axes, labels = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00"),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00", **{axis: changed}),
    ])
    assert compare_runs.check_comparable(axes, labels) == [axis]


def test_an_application_change_does_not_block_the_comparison():
    _, axes, labels = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00"),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00",
               app_prompt_version="investigation_v2"),
    ])
    assert compare_runs.check_comparable(axes, labels) == []
    assert "prompt_version investigation_v1->investigation_v2" in \
        compare_runs.app_change(axes, "a", "b")


def test_one_label_covering_two_configurations_is_refused():
    # Same label, two different judges: not one run, and grouping them would
    # average across a changed instrument.
    _, axes, labels = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00"),
        _score("a", "hallucination", 0.2, "2026-07-31T10:00:01+00:00",
               judge_model="gpt-5.4"),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00"),
    ])
    assert "judge_model" in compare_runs.check_comparable(axes, labels)


def test_unknown_build_is_flagged_when_nothing_else_moved():
    _, axes, _ = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00",
               app_build=common.UNAVAILABLE),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00",
               app_build=common.UNAVAILABLE),
    ])
    assert compare_runs.app_change(axes, "a", "b") == "no fingerprint change (build unknown)"


def test_identical_fingerprints_report_no_application_change():
    _, axes, _ = _grouped([
        _score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00"),
        _score("b", "bias", 0.2, "2026-07-31T11:00:00+00:00"),
    ])
    assert compare_runs.app_change(axes, "a", "b") == "no application change"


class _Subject:
    def __init__(self, kind, id, trace_id=None):
        self.kind, self.id, self.trace_id = kind, id, trace_id


def test_observation_subject_yields_both_drill_down_keys():
    assert compare_runs.subject_ids(_Subject("observation", "obs1", "trace1")) == \
        ("trace1", "obs1")


def test_trace_subject_has_no_observation():
    assert compare_runs.subject_ids(_Subject("trace", "trace1")) == ("trace1", None)


def test_unknown_subject_yields_nothing_rather_than_a_borrowed_id():
    # A session or experiment id is not a trace id. Reusing one would produce
    # a drill-down link that resolves to the wrong thing.
    assert compare_runs.subject_ids(_Subject("session", "sess1")) == (None, None)
    assert compare_runs.subject_ids(None) == (None, None)


def test_explain_prints_the_reason_and_both_places_to_look(capsys):
    scores = [_score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00")]
    scores[0]["comment"] = "the rationale mentions nationality"
    scores[0]["trace_id"], scores[0]["observation_id"] = "trace1", "obs1"
    assert compare_runs.explain(scores, "bias", "a") == 0
    out = capsys.readouterr().out
    assert "the rationale mentions nationality" in out
    assert "/api/agent/trace/1" in out      # the unmasked, local evidence
    assert "/api/eval/export/1" in out
    assert "trace_id=trace1" in out          # offline: ids, not a URL


def test_explain_names_what_the_run_did_score_when_the_metric_is_wrong(capsys):
    scores = [_score("a", "bias", 0.1, "2026-07-31T10:00:00+00:00")]
    assert compare_runs.explain(scores, "hallucination", "a") == 1
    assert "It scored: ['bias']" in capsys.readouterr().err


def test_explain_does_not_invent_a_curl_without_a_run_id(capsys):
    scores = [_score("a", "contextual_recall", 0.8, "2026-07-31T10:00:00+00:00",
                     app_run_id=common.UNAVAILABLE)]
    compare_runs.explain(scores, "contextual_recall", "a")
    out = capsys.readouterr().out
    assert "no run id recorded" in out
    assert "/api/agent/trace/" not in out


def test_latency_uses_a_relative_notability_rule():
    # 0.05 absolute is meaningless on a millisecond scale.
    assert compare_runs.notable("app_latency_ms", 7000, 9200)
    assert not compare_runs.notable("app_latency_ms", 7000, 7100)
    assert compare_runs.notable("bias", 0.10, 0.20)
    assert not compare_runs.notable("bias", 0.10, 0.12)
