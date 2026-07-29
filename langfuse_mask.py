"""PII masking for anything sent to Langfuse.

docs/observability-plan.md §5 requires this to exist and be tested as code
before any run sends data, and to fail closed: an exception inside a
Langfuse mask callback drops the entire export batch silently, so a bug in
masking must never become a bug in whether data gets exported unmasked.

Two layers, both applied, for defense in depth:

1. `mask_payload` - called explicitly by run_experiment.py on every value
   before it is handed to the SDK at all (task output, evaluator input,
   metadata). This does not depend on any Langfuse SDK wiring being correct,
   so it protects data even if the SDK-level hook below is misconfigured or
   its signature has drifted from what's documented here.
2. `mask` - the Langfuse SDK mask callback (`Langfuse(mask=mask)`).
   Signature confirmed 2026-07-29 by inspecting the installed
   `langfuse==4.14.1` package directly (`langfuse.types.MaskFunction`, a
   `Protocol` with `def __call__(self, *, data: Any, **kwargs) -> Any`).
   `langfuse==4.14.1` also offers `mask_otel_spans`, which operates over a
   dict of whole OTEL spans and returns patches - a materially more
   involved contract. This module deliberately uses the simpler `mask`
   hook so the callback stays a pure, easily-testable function; revisit
   only if `mask` is ever removed in a future major version (re-check by
   inspecting `langfuse.types.MaskFunction` again after any version bump -
   don't assume the signature is still current).

Cross-check `_REDACT_KEY_FRAGMENTS` against live `/openapi.json` schemas
before relying on it for anything beyond the current synthetic seed
scenarios - it is a starting point, not a guaranteed-exhaustive list. See
plan.md "Open items to revisit."

**Naming convention experiment modules must follow.** Redaction matches on
the substring "name" anywhere in a key, which is deliberately broad enough
to catch `name`, `customer_name`, `listed_name`, etc. That same substring
also appears in non-PII identifiers this codebase otherwise wants visible
in a trace - a tool's own identifier, an MCP server's identifier. Verified
directly against the installed SDK (2026-07-29): `mask()` receives whole
nested Python objects, not flattened scalars, so this collision is real,
not theoretical - `{"name": "risk_screening.screen_sanctions_pep",
"input_parameters": {"name": "Jane Doe"}}` would have *both* fields
redacted. Experiment modules avoid the collision at the source: use `tool`
for a tool's namespaced identifier and `server` for an MCP server's
identifier in any dict handed to a task/evaluator, never `name` or
`tool_name` (which still contains the substring). Reserve DeepEval's own
`ToolCall(name=...)` construction, which does require that exact field
name, to the evaluator's local reconstruction step, built from the
already-real in-memory values - never from a dict that passed through
`mask()`.
"""

from __future__ import annotations

from typing import Any

# Field-name fragments (case-insensitive substring match) whose values are
# always redacted, regardless of nesting depth or container type. Matches
# customer name, incorporation_or_dob, identifiers.dob, beneficiary names,
# matches[].listed_name - the fields named in docs/observability-plan.md §5.
_REDACT_KEY_FRAGMENTS = (
    "name",
    "dob",
    "date_of_birth",
    "incorporation_or_dob",
    "listed_name",
    "nationality",
)

_REDACTED = "***MASKED***"
_MASK_FAILURE_SENTINEL = "***MASKING_FAILED_REDACTED***"


def _should_redact_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _REDACT_KEY_FRAGMENTS)


def _mask_recursive(value: Any, key: str | None = None) -> Any:
    if key is not None and value is not None and _should_redact_key(key):
        return _REDACTED
    if isinstance(value, dict):
        return {k: _mask_recursive(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_recursive(v, key) for v in value]
    return value


def mask_payload(value: Any) -> Any:
    """Mask PII-bearing fields in a value before it is sent anywhere.

    Call this explicitly on task outputs, evaluator inputs, and metadata in
    run_experiment.py - it is the primary control, not a backup for the SDK
    hook below.

    Fails closed: never raises. On any error the whole value is replaced
    with a fixed sentinel rather than exported unmasked.
    """
    try:
        return _mask_recursive(value)
    except Exception:
        return _MASK_FAILURE_SENTINEL


def mask(*, data: Any = None, **_kwargs: Any) -> Any:
    """Langfuse SDK legacy mask callback: `Langfuse(mask=mask)`.

    Same fail-closed contract as `mask_payload`, kept as a separate
    entry point because the SDK calls this with keyword-only `data` on
    individual span attribute values, not on our own task/evaluator
    payloads directly.
    """
    return mask_payload(data)
