# Ask: expose a build identifier on `GET /api/health`

**Status: requested, not yet implemented.** Raised 2026-07-31.
**Bucket 2** per [`CLAUDE.md`](../../CLAUDE.md) — valuable, but it is the
application's job. This document names exactly what the application would
need to expose and stops there; it deliberately does not design the
application-side implementation.

**Target repository:** `aml-kyc-agentic-platform` (separate repo — this
harness has no source or filesystem access to it).

**Why this exists as a document:** [`plan.md`](../../plan.md) §11 item 3 and
§2 (Phase R0.4) both depend on this field. Until it lands,
`experiments/common.run_context()` records `app_build: "unavailable"` and
Phase R2's comparison treats that as a warning, because it means
application changes may be invisible to regression attribution.

**How to use the prompt below:** it is written to be pasted verbatim into a
Claude Code session (or handed to an engineer) working in the application
repository. It states a contract and acceptance criteria, and leaves every
implementation choice — and several design decisions — to whoever owns that
code. Keep it that way if you edit it: an ask that dictates internals from
this repository is both out of bounds and less likely to be accepted.

---

## The prompt

```markdown
# Request: expose a build identifier on GET /api/health

## Context — why this is being asked for

An external black-box acceptance-test harness runs 14 DeepEval metrics
against this application and stores each run's scores for regression
comparison. To decide whether a score drop is a real regression, it records
a per-run "application fingerprint" from `GET /api/agent/trace/{run_id}`:
`model`, `prompt_version`, and `model_configuration`.

That fingerprint has a blind spot. A change to retrieval logic, a tool
implementation, a scoring threshold, or a dependency bump will move scores
while `model`, `prompt_version` and `model_configuration` all stay
byte-identical. The comparison then reports a real regression as "no
application change," which is worse than reporting nothing — it actively
misattributes the cause.

The fix is one field: something that changes whenever the deployed code
changes.

## The request

Add a build identifier to the `GET /api/health` response.

Preferred field name: `build_version` (or `git_sha` — your call, just tell
us which). Type: string. Example: `"a1b2c3d"` or `"1.4.2+a1b2c3d"`.

`GET /api/health` is preferred over a new endpoint because the harness
already calls it once per run as its reachability check, so this costs zero
additional requests. If you'd rather put it elsewhere (e.g. `/api/test/health`,
or a new `/api/version`), that's fine — counter-propose and we'll consume
whatever you settle on.

## Required semantics

1. **Changes when the deployed code changes.** This is the entire point. A
   value that stays constant across deploys is worse than absent, because
   it looks like a working signal.
2. **Stable across restarts and replicas of the same build.** Two pods
   running the same image must report the same value, and a restart must
   not change it.
3. **Distinct from `schema_version`.** `schema_version` is an API-contract
   version and correctly stays fixed across code changes — it cannot serve
   this purpose.
4. **Resolved at build/startup, never per request.** See constraints.

## Constraints

- **Additive and optional.** Existing consumers must not break. Do not
  rename, retype, or remove `status`, `schema_version`, or `eval_tracing`.
- **Must never make `/api/health` fail, hang, or slow down.** This endpoint
  is a liveness/readiness signal. Do not shell out to `git` on the request
  path, do not touch the filesystem per request, do not add I/O. Resolve
  the value once (build arg, env var baked into the image, generated file,
  package metadata) and serve it from memory.
- **Must degrade gracefully.** Builds outside a git checkout, shallow
  clones, or missing build args must yield a defined value (e.g.
  `"unknown"`) — never a crash, never an exception, never a 500. Decide and
  document what a dirty working tree reports.
- **No secrets or internal paths.** A commit SHA and/or semver is fine;
  don't include build-host paths, tokens, or internal URLs.
- **Update the OpenAPI schema.** The harness treats `/openapi.json` as the
  source of truth for what's callable and will read the field's presence
  and type from there. An undocumented field is not consumable.

## Acceptance criteria

- [ ] `GET /api/health` returns the new field alongside the existing
      `status`, `schema_version`, `eval_tracing`.
- [ ] The field appears in `/openapi.json` under the health response schema,
      with its type.
- [ ] Two consecutive calls return the same value.
- [ ] A restart of the same build returns the same value.
- [ ] Two different builds return different values.
- [ ] A build with no git metadata available returns the documented fallback
      rather than erroring.
- [ ] Response time and failure behaviour of `/api/health` are unchanged.
- [ ] Existing consumers parsing the old response still work.

## Explicitly out of scope

Not asking for: a new authenticated endpoint, build metadata on any other
endpoint, per-request version negotiation, exposing dependency versions,
or any change to `/api/agent/trace/{run_id}`.

## One decision for you

`/api/health` may be unauthenticated. A commit SHA on an unauthenticated
endpoint is a mild information disclosure (it identifies the exact source
revision). If that's unacceptable in your threat model, an opaque but
change-detecting value — a build number, or a hash of the SHA — satisfies
the requirement equally well. The harness only needs the value to *change
when the code changes*; it never needs to interpret it.
```

---

## Current state, for reference

Verified against a running instance 2026-07-31:

```
GET /api/health   -> {"status":"ok","schema_version":"1.0.0","eval_tracing":true}
```

`HealthResponse` in `/openapi.json` has exactly three properties:
`status`, `schema_version`, `eval_tracing`. No build, release, or version
identifier is exposed anywhere in the OpenAPI surface.

The fingerprint fields this ask supplements are served, and confirmed
working, on `GET /api/agent/trace/{run_id}`:

```
model:               "openai/gpt-4o-mini"
prompt_version:      "investigation_v1"
model_configuration: {"requested_model":"openai/gpt-4o-mini","temperature":0.0, ...}
```

## What this harness does when the field is absent

Per `CLAUDE.md`'s fail-loudly rule, the harness must never infer a build
identity it cannot read. Until this lands:

- `run_context()` records `app_build: "unavailable"` — the key is always
  present, never omitted, never guessed.
- `compare_runs.py` (Phase R2) prints a warning on any comparison where
  both runs report `"unavailable"`, stating that application changes may be
  invisible to attribution. It still compares — the measurement axes are
  unaffected — but it does not claim "no application change."

## Follow-ups once it lands

1. Consume it in `run_context()` as an **application axis** (a change means
   the comparison is still valid and this is the explanatory variable), not
   a measurement axis — see [`plan.md`](../../plan.md) §0.
2. Drop the "may be invisible" warning from `compare_runs.py`.
3. Record the agreed field name and fallback value here, and update
   [`plan.md`](../../plan.md) §11 item 3 to done.
4. Re-check `/openapi.json`, per `CLAUDE.md` — this repo is not notified
   when the application's surface changes.
