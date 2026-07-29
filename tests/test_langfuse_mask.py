"""Unit tests for langfuse_mask.py.

Run with: uv sync --extra dev && uv run pytest tests/test_langfuse_mask.py -v

Every PII-bearing field named in docs/observability-plan.md §5 gets its own
fixture, plus the fail-closed guarantee that a masking exception never
propagates and never results in unmasked data.
"""

from __future__ import annotations

import langfuse_mask


def test_customer_name_is_masked():
    payload = {"customer": {"name": "Jane Alice Doe", "risk_score": 0.7}}
    masked = langfuse_mask.mask_payload(payload)
    assert masked["customer"]["name"] == "***MASKED***"
    assert masked["customer"]["risk_score"] == 0.7


def test_incorporation_or_dob_is_masked():
    payload = {"identifiers": {"incorporation_or_dob": "1985-03-14"}}
    masked = langfuse_mask.mask_payload(payload)
    assert masked["identifiers"]["incorporation_or_dob"] == "***MASKED***"


def test_nested_dob_is_masked():
    payload = {"identifiers": {"dob": "1990-01-01", "country": "NG"}}
    masked = langfuse_mask.mask_payload(payload)
    assert masked["identifiers"]["dob"] == "***MASKED***"
    assert masked["identifiers"]["country"] == "NG"


def test_beneficiary_names_in_list_are_masked():
    payload = {
        "beneficiaries": [
            {"name": "Beneficiary One", "share_pct": 50},
            {"name": "Beneficiary Two", "share_pct": 50},
        ]
    }
    masked = langfuse_mask.mask_payload(payload)
    assert [b["name"] for b in masked["beneficiaries"]] == ["***MASKED***", "***MASKED***"]
    assert [b["share_pct"] for b in masked["beneficiaries"]] == [50, 50]


def test_matches_listed_name_is_masked():
    payload = {
        "matches": [
            {"listed_name": "Some Sanctioned Entity", "score": 0.92, "list": "OFAC-SDN"}
        ]
    }
    masked = langfuse_mask.mask_payload(payload)
    assert masked["matches"][0]["listed_name"] == "***MASKED***"
    assert masked["matches"][0]["score"] == 0.92
    assert masked["matches"][0]["list"] == "OFAC-SDN"


def test_nationality_is_masked():
    payload = {"customer": {"nationality": "Nigerian"}}
    masked = langfuse_mask.mask_payload(payload)
    assert masked["customer"]["nationality"] == "***MASKED***"


def test_non_pii_fields_pass_through_unmodified():
    payload = {
        "case_id": 42,
        "scenario_id": "s6",
        "title": "Structuring behaviour",
        "seed_version": "scenarios-v1",
    }
    assert langfuse_mask.mask_payload(payload) == payload


def test_plain_string_with_no_key_context_is_not_redacted():
    # A bare string (e.g. the metric's input/output) has no dict key to
    # match against, so it passes through - callers are responsible for
    # masking before flattening structured data into free text.
    assert langfuse_mask.mask_payload("What indicators identify structuring?") == (
        "What indicators identify structuring?"
    )


def test_redaction_is_key_based_not_value_based():
    # A field literally called "name" holding a non-name-shaped value is
    # still redacted - this masker matches on key, deliberately, since it
    # cannot know a value "looks like" a name from content alone.
    payload = {"name": "N/A"}
    assert langfuse_mask.mask_payload(payload)["name"] == "***MASKED***"


def test_none_values_under_a_redacted_key_are_left_as_none():
    # Redacting None to a sentinel string would be a false signal that a
    # name was present when the field was simply absent.
    payload = {"name": None}
    assert langfuse_mask.mask_payload(payload)["name"] is None


def test_fails_closed_on_unexpected_exception(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated masking bug")

    monkeypatch.setattr(langfuse_mask, "_mask_recursive", _boom)
    result = langfuse_mask.mask_payload({"name": "should never appear"})
    assert result == "***MASKING_FAILED_REDACTED***"
    assert "should never appear" not in str(result)


def test_sdk_mask_callback_delegates_to_mask_payload():
    result = langfuse_mask.mask(data={"name": "Jane Doe"})
    assert result == {"name": "***MASKED***"}


def test_sdk_mask_callback_also_fails_closed(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated masking bug")

    monkeypatch.setattr(langfuse_mask, "_mask_recursive", _boom)
    result = langfuse_mask.mask(data={"name": "should never appear"})
    assert result == "***MASKING_FAILED_REDACTED***"


def test_tool_call_shape_uses_tool_and_server_keys_not_name():
    # Regression test for the naming-convention note in the module
    # docstring: "name" substring-matches broadly on purpose (to catch
    # customer_name, listed_name, etc.), which would also redact a tool's
    # own identifier if experiment modules used "name" or "tool_name" for
    # it. Convention is "tool"/"server" instead - confirmed here to survive
    # unmasked, while a real PII field nested alongside it still gets
    # redacted.
    payload = {
        "tool": "risk_screening.screen_sanctions_pep",
        "server": "risk_screening",
        "input_parameters": {"name": "Jane Doe", "dob": "1990-01-01"},
    }
    masked = langfuse_mask.mask_payload(payload)
    assert masked["tool"] == "risk_screening.screen_sanctions_pep"
    assert masked["server"] == "risk_screening"
    assert masked["input_parameters"]["name"] == "***MASKED***"
    assert masked["input_parameters"]["dob"] == "***MASKED***"
