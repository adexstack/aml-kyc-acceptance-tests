"""Shared HTTP/env helpers for the Langfuse experiment runner.

Mirrors the boilerplate every notebook duplicates (`env()`, `api()`, the
seed-version guard, `AML_RESET_BEFORE_RUN` handling) so the metric modules
under this package don't each re-derive it. This is the accepted
duplication *of the pattern* from docs/observability-plan.md §3.1 - this
package imports nothing from notebooks/, and notebooks/ import nothing from
here.

It also owns run identity and the application fingerprint that make one
run's scores comparable with another's - `run_context()`, `record_app_run()`
and the `app_latency` evaluator, all documented in the section at the foot
of this file (plan.md §R0-R1).

Masking note: nothing in this module masks anything. Task/evaluator data
must stay real so DeepEval scores the actual values - see run_experiment.py's
module docstring and langfuse_mask.py. Masking happens once, at the
Langfuse SDK's export boundary.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
_env_file = _REPO_ROOT / ".env"
if _env_file.is_file():
    load_dotenv(_env_file)


class MissingConfiguration(RuntimeError):
    """Raised when a required environment variable is absent."""


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name) or default
    if required and not value:
        raise MissingConfiguration(
            f"Environment variable {name!r} is not set.\n"
            f"Copy .env.example to .env and fill it in, or export {name} before running."
        )
    return value or ""


API_BASE = env("AML_API_BASE_URL", "http://localhost:8000").rstrip("/")
API_TIMEOUT_S = float(env("AML_API_TIMEOUT_S", "180"))
EXPECTED_SEED_VERSION = env("AML_EXPECTED_SEED_VERSION", "scenarios-v1")
RESET_BEFORE_RUN = env("AML_RESET_BEFORE_RUN", "false").lower() in ("1", "true", "yes")
JUDGE_MODEL = env("DEEPEVAL_JUDGE_MODEL", "gpt-5.4-mini")

# The API contract these tests were written against. The application may
# serve a HIGHER minor version - a MINOR bump is additive by the
# application's own contract policy, and 1.1.0 (which added
# HealthResponse.build_version) was verified additive field-by-field on
# 2026-07-31. Only a MAJOR change breaks us. See check_schema_version().
EXPECTED_SCHEMA_VERSION = "1.0.0"

# The grouping key for every score this process emits (plan.md §R0.1). It
# must be set deliberately to be useful ("before-planner-fix"); the
# timestamp default only guarantees two runs are never conflated.
RUN_LABEL = env("AML_RUN_LABEL") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

API_KEYS = {
    "analyst": env("AML_API_KEY_ANALYST") or env("AML_API_KEY"),
    "eval_reader": env("AML_API_KEY_EVAL_READER") or env("AML_API_KEY"),
    "test_operator": env("AML_API_KEY_TEST_OPERATOR") or env("AML_API_KEY"),
}


class ApiError(RuntimeError):
    """A non-2xx response, carrying the application's error envelope."""


def api(method: str, path: str, *, role: str = "analyst", json_body=None,
        params=None, expect_status: int | None = None):
    """Call the application API and return parsed JSON. Mirrors the
    notebooks' own `api()` helper, trimmed to what the experiment modules
    need - see any notebook's config cell for the fuller version with more
    diagnostic hints."""
    headers = {"Accept": "application/json"}
    key = API_KEYS.get(role, "")
    if key:
        headers["X-API-Key"] = key

    url = f"{API_BASE}{path}"
    try:
        response = httpx.request(method, url, headers=headers, json=json_body,
                                  params=params, timeout=API_TIMEOUT_S)
    except httpx.ConnectError as exc:
        raise ApiError(
            f"Could not connect to {url}. Is the application running at {API_BASE!r}?\n"
            f"Underlying error: {exc}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ApiError(f"{method} {url} timed out after {API_TIMEOUT_S}s: {exc!r}") from exc

    if response.status_code >= 400:
        try:
            envelope = response.json()
        except ValueError:
            envelope = {"raw_body": response.text[:1000]}
        raise ApiError(f"{method} {url} -> HTTP {response.status_code}\n"
                        f"  envelope: {envelope}")

    if expect_status is not None and response.status_code != expect_status:
        raise ApiError(f"{method} {url} -> expected HTTP {expect_status}, "
                        f"got {response.status_code}")

    if not response.content:
        return None
    return response.json()


_health_cache: dict | None = None


def health() -> dict:
    """GET /api/health, once per process.

    The health payload is the source for two run-context fields
    (`schema_version`, `build_version`) that every score is stamped with, so
    it is fetched once and cached rather than per evaluator. It is also the
    suite's reachability check.
    """
    global _health_cache
    if _health_cache is None:
        _health_cache = api("GET", "/api/health")
    return _health_cache


def _semver(value: str) -> tuple[int, int, int]:
    parts = (value or "").split("+")[0].split(".")
    try:
        return tuple(int(p) for p in (parts + ["0", "0", "0"])[:3])  # type: ignore[return-value]
    except ValueError as exc:
        raise RuntimeError(
            f"Cannot parse {value!r} as a semantic version. The application's contract "
            f"version is expected to be MAJOR.MINOR.PATCH."
        ) from exc


def check_schema_version() -> str:
    """Guard the API contract version, minor-compatibly, and return it.

    A MINOR bump is additive by the application's own contract policy - the
    1.0.0 -> 1.1.0 bump on 2026-07-31 added exactly one field
    (`HealthResponse.build_version`) and removed or retyped nothing. Failing
    or warning on that would turn a real guard into noise that people learn
    to scroll past. A MAJOR change is a different contract and raises; an
    application OLDER than the harness expects warns, because goldens may
    reference fields it does not serve yet.
    """
    served = health().get("schema_version") or ""
    served_major, served_minor, _ = _semver(served)
    expected_major, expected_minor, _ = _semver(EXPECTED_SCHEMA_VERSION)

    if served_major != expected_major:
        raise RuntimeError(
            f"Refusing to run: the application serves contract version {served!r}, these "
            f"tests were written against {EXPECTED_SCHEMA_VERSION!r}. A MAJOR change means "
            f"fields may have been removed or retyped - re-derive the goldens against the "
            f"new contract rather than scoring against it blind."
        )
    if served_minor < expected_minor:
        print(
            f"WARNING: the application serves contract version {served}, older than the "
            f"{EXPECTED_SCHEMA_VERSION} these tests were written against. Fields the "
            f"goldens rely on may not exist yet."
        )
    return served


def load_scenarios() -> dict:
    """Fetch and validate the synthetic seed scenarios.

    Refuses to proceed - and therefore refuses to send anything to Langfuse
    Cloud - unless the target instance reports the expected synthetic seed
    version. This is the runtime guard docs/observability-plan.md §5 and
    plan.md §1.1 require, not just a policy note.

    Deliberately NOT cached: maybe_reset() drops and recreates every table
    between experiments, so case ids from a previous call are not
    necessarily valid after one. The scenario count is recorded here (a
    measurement axis - a run over a different number of scenarios is not
    comparable) so run_context() never has to make a call of its own.
    """
    global _scenario_count
    check_schema_version()
    scenarios = {s["scenario_id"]: s for s in api("GET", "/api/eval/scenarios",
                                                    role="eval_reader")}
    _scenario_count = str(len(scenarios))
    seed_versions = {s["seed_version"] for s in scenarios.values()}
    if seed_versions != {EXPECTED_SEED_VERSION}:
        raise RuntimeError(
            f"Refusing to run: application reports seed_version(s) {seed_versions}, "
            f"expected only {EXPECTED_SEED_VERSION!r}. This runner is only authorised to "
            f"send data to Langfuse Cloud when the target instance serves the known "
            f"synthetic seed scenarios (docs/observability-plan.md §5, plan.md §1.1). "
            f"Revisit the hosting decision in plan.md §0 before pointing this at anything "
            f"else."
        )
    return scenarios


def item_input(item) -> dict:
    """Langfuse passes local dataset items as plain dicts but its own
    DatasetItem objects (when using a remote Langfuse Dataset instead of
    local data) expose `.input` as an attribute - handle both, per the
    experiments-via-sdk docs' own example."""
    return item["input"] if isinstance(item, dict) else item.input


def maybe_reset() -> None:
    """Mirror each notebook's own reset cell.

    Every notebook independently resets when AML_RESET_BEFORE_RUN is true,
    so its own expected-tool-plan derivation is valid against a first run
    (the planner skips tools already completed for a case). Each experiment
    module calls this itself, for the same reason and at the same point in
    the sequence as the notebook it was ported from - running several
    investigate-based experiments back to back without a reset between them
    means only the first genuinely sees first-run planner behaviour. This
    duplicates a reset per metric rather than sharing one; slower, but
    faithful to the source notebook's own assumption.
    """
    if RESET_BEFORE_RUN:
        api("POST", "/api/dev/reset", role="test_operator")


# ---------------------------------------------------------------------------
# Run identity and the application fingerprint (plan.md §R0.1, §R0.2).
#
# Every score this package emits carries the dict run_context() returns, so
# a stored score says which run it belongs to and what produced it. The keys
# split into two categories and the distinction is the whole point
# (plan.md §0):
#
#   measurement axes - if one of these changed, the two runs are NOT
#     comparable: the instrument moved, not the thing being measured.
#     compare_runs.py refuses on any difference here.
#   application axes - if one of these changed, the comparison is valid and
#     THIS is the explanatory variable you were looking for.
#
# Fail loudly, never infer (CLAUDE.md): a field the API does not serve is
# recorded as the literal "unavailable", never omitted and never guessed.
# "not_applicable" is a different statement - it means the call genuinely
# has no such property (the retrieval endpoint runs no LLM, so it has no
# model or prompt version). Do not collapse the two.
# ---------------------------------------------------------------------------

MEASUREMENT_AXES = ("run_label", "judge_model", "seed_version", "schema_version",
                    "reset_before_run", "harness_sha", "scenario_count")
APPLICATION_AXES = ("app_model", "app_prompt_version", "app_temperature", "app_build",
                    "app_run_id")

UNAVAILABLE = "unavailable"
NOT_APPLICABLE = "not_applicable"

_scenario_count: str = UNAVAILABLE
_harness_sha_cache: str | None = None
_trace_cache: dict[str, dict] = {}
_app_runs: dict[str, dict] = {}


def harness_sha() -> str:
    """This repository's commit, so "the test changed" is distinguishable
    from "the application changed". A dirty tree is reported as such - an
    uncommitted harness is not a reproducible measurement."""
    global _harness_sha_cache
    if _harness_sha_cache is None:
        try:
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_REPO_ROOT,
                                 capture_output=True, text=True, timeout=10, check=True
                                 ).stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=_REPO_ROOT,
                                   capture_output=True, text=True, timeout=10, check=True
                                   ).stdout.strip()
            _harness_sha_cache = f"{sha}.dirty" if dirty else sha
        except (OSError, subprocess.SubprocessError):
            # Running from a copied directory rather than a checkout is a
            # supported deployment (README portability rules), so this is
            # not an error - but it is not a known harness version either.
            _harness_sha_cache = UNAVAILABLE
    return _harness_sha_cache


def app_build() -> str:
    """The application's build identifier from GET /api/health.

    Added to the application on 2026-07-31 at this harness's request - see
    docs/asks/build-version-request.md. It is the only fingerprint field
    that moves when retrieval logic, a tool implementation or a threshold
    changes; `model` and `prompt_version` do not.

    The application documents "<version>+unknown" as its fallback when the
    image was built without git metadata. That value is a CONSTANT: it does
    not change when the code changes, so recording it verbatim would be a
    signal that looks like it works and does not. It is reported as
    "unavailable" instead, which is what it actually is.
    """
    value = (health().get("build_version") or "").strip()
    if not value or value.split("+")[-1].startswith("unknown"):
        return UNAVAILABLE
    return value


def trace(run_id) -> dict:
    """GET /api/agent/trace/{run_id}, cached per run.

    Several callers want different parts of the same trace (the model
    configuration for the fingerprint, the per-step timings for the latency
    scores, deepeval_trace for PlanAdherence). Fetching it once per run
    keeps plan.md §10's budget of one extra read per run honest.
    """
    key = str(run_id)
    if key not in _trace_cache:
        _trace_cache[key] = api("GET", f"/api/agent/trace/{key}", role="eval_reader")
    return _trace_cache[key]


def record_app_run(response: dict) -> str:
    """Record the application fingerprint carried by a response the task
    already fetched, and return the key evaluators pass to run_context().

    No extra API call: InvestigateResponse and QueryResponse both return
    `run_id`, `model`, `prompt_version` and `latency_ms` inline, and
    RetrieveResponse returns `retrieval_run_id` and `latency_ms`. Only the
    sampling temperature and the per-phase latency split need the trace
    endpoint, and both are fetched lazily and cached.
    """
    if "run_id" in response:  # an agent run: investigate or rag/query
        key = str(response["run_id"])
        _app_runs[key] = {
            "app_run_id": key,
            "app_model": response.get("model") or UNAVAILABLE,
            "app_prompt_version": response.get("prompt_version") or UNAVAILABLE,
            "latency_ms": response.get("latency_ms"),
            "agent_run": True,
        }
        return key
    if "retrieval_run_id" in response:  # POST /api/rag/retrieve - no LLM in this path
        key = f"retrieval:{response['retrieval_run_id']}"
        _app_runs[key] = {
            "app_run_id": str(response["retrieval_run_id"]),
            "app_model": NOT_APPLICABLE,
            "app_prompt_version": NOT_APPLICABLE,
            "latency_ms": response.get("latency_ms"),
            "agent_run": False,
        }
        return key
    raise RuntimeError(
        "record_app_run() was handed a response carrying neither `run_id` nor "
        "`retrieval_run_id`, so there is no application run to fingerprint. Pass the "
        "response from POST /api/cases/{id}/investigate, POST /api/rag/query or "
        "POST /api/rag/retrieve - do not synthesise an identifier."
    )


def last_app_run() -> str | None:
    """The most recently recorded application run, for callers that want the
    fingerprint after an experiment has finished (the run manifest) rather
    than per score. Returns None before any call has been made."""
    return next(reversed(_app_runs), None) if _app_runs else None


def run_context(app_run: str | None = None,
                item_metadata: dict | None = None) -> dict[str, str]:
    """The flat dict every Evaluation is stamped with (plan.md §R0.2).

    Values are flat strings on purpose: Langfuse flattens and serialises
    metadata on export (`_flatten_and_serialize_metadata_values`), so
    anything nested comes back as an unqueryable blob. Note the round trip
    is not lossless in the other direction either - Langfuse returns them
    parsed, so "true" comes back as True; any reader must normalise.

    `item_metadata` is the dataset item's own metadata, which the SDK passes
    to every evaluator as `metadata`. Only the scenario id is taken from it,
    and it is NOT an axis: it identifies which item a score belongs to, for
    drill-down (`compare_runs.py --explain`), not which run. Items that are
    genuinely caseless - the summarization and retrieval experiments query a
    document, not a scenario - record "not_applicable".
    """
    context = {
        "run_label": RUN_LABEL,
        "judge_model": JUDGE_MODEL,
        "seed_version": EXPECTED_SEED_VERSION,
        "schema_version": health().get("schema_version") or UNAVAILABLE,
        "reset_before_run": str(RESET_BEFORE_RUN).lower(),
        "harness_sha": harness_sha(),
        "scenario_count": _scenario_count,
        "item_scenario": str((item_metadata or {}).get("scenario_id") or NOT_APPLICABLE),
        "app_build": app_build(),
        "app_model": UNAVAILABLE,
        "app_prompt_version": UNAVAILABLE,
        "app_temperature": UNAVAILABLE,
        "app_run_id": UNAVAILABLE,
    }

    record = _app_runs.get(app_run or "")
    if record is None:
        return context

    context["app_run_id"] = record["app_run_id"]
    context["app_model"] = record["app_model"]
    context["app_prompt_version"] = record["app_prompt_version"]

    if not record["agent_run"]:
        context["app_temperature"] = NOT_APPLICABLE
        return context

    # The one field that is not already in hand. A trace that cannot be read
    # leaves the key present and honest rather than defaulting the value -
    # a temperature change looks exactly like a quality change without it.
    try:
        configuration = trace(record["app_run_id"]).get("model_configuration") or {}
    except ApiError:
        return context
    temperature = configuration.get("temperature")
    context["app_temperature"] = UNAVAILABLE if temperature is None else str(temperature)
    return context


def app_latency(*, output, **_kwargs):
    """Shared evaluator: the application's OWN server-side timings, as scores.

    Attached to the `evaluators` list of every experiment whose task makes
    exactly one application call that the metric is about, so latency lives
    in the same store, dashboards and monitors as the quality scores
    (plan.md §R1.2).

    Never Langfuse span duration: that measures harness wall-clock - the
    application call PLUS the judge call PLUS overhead - and charting it as
    application latency is exactly the conflation
    docs/performance-latency-plan.md §5 forbids.

    A phase score is emitted only when the run actually has steps of that
    type. A run that called no tools has no tool-call latency; recording
    0 ms would be a fabricated measurement, not a fast one.
    """
    from langfuse import Evaluation

    key = (output or {}).get("app_run")
    record = _app_runs.get(key or "")
    if record is None:
        raise RuntimeError(
            "app_latency was attached to an experiment whose task did not return an "
            "`app_run` key. Call common.record_app_run(response) in the task and put its "
            "return value in the returned dict - do not infer a latency from elsewhere."
        )

    context = run_context(key, _kwargs.get("metadata"))
    if record["latency_ms"] is None:
        raise RuntimeError(
            f"The application returned no latency_ms for run {record['app_run_id']}, so "
            f"there is no server-side timing to score. Check GET /api/agent/trace/"
            f"{record['app_run_id']} rather than substituting harness wall-clock."
        )

    evaluations = [Evaluation(name="app_latency_ms", value=float(record["latency_ms"]),
                              metadata=context)]
    if not record["agent_run"]:
        return evaluations

    try:
        steps = trace(record["app_run_id"]).get("steps") or []
    except ApiError:
        return evaluations

    for step_type, score_name in (("retrieval", "app_latency_retrieval_ms"),
                                  ("tool_call", "app_latency_tool_call_ms"),
                                  ("synthesis", "app_latency_synthesis_ms")):
        timings = [s["latency_ms"] for s in steps
                   if s.get("type") == step_type and s.get("latency_ms") is not None]
        if timings:
            evaluations.append(Evaluation(name=score_name, value=float(sum(timings)),
                                          metadata=context))
    return evaluations
