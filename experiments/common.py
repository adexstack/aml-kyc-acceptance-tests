"""Shared HTTP/env helpers for the Langfuse experiment runner.

Mirrors the boilerplate every notebook duplicates (`env()`, `api()`, the
seed-version guard, `AML_RESET_BEFORE_RUN` handling) so the metric modules
under this package don't each re-derive it. This is the accepted
duplication *of the pattern* from docs/observability-plan.md §3.1 - this
package imports nothing from notebooks/, and notebooks/ import nothing from
here.

Masking note: nothing in this module masks anything. Task/evaluator data
must stay real so DeepEval scores the actual values - see run_experiment.py's
module docstring and langfuse_mask.py. Masking happens once, at the
Langfuse SDK's export boundary.
"""

from __future__ import annotations

import os
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


def load_scenarios() -> dict:
    """Fetch and validate the synthetic seed scenarios.

    Refuses to proceed - and therefore refuses to send anything to Langfuse
    Cloud - unless the target instance reports the expected synthetic seed
    version. This is the runtime guard docs/observability-plan.md §5 and
    plan.md §1.1 require, not just a policy note.
    """
    scenarios = {s["scenario_id"]: s for s in api("GET", "/api/eval/scenarios",
                                                    role="eval_reader")}
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
