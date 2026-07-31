# Ask: expose a build identifier on `GET /api/health`

**Status: implemented and verified 2026-07-31 — with one deployment-side gap
that means it does not yet do its job on the instance this harness targets.**
Raised and delivered the same day. See "What landed" below before reading the
prompt, which is kept verbatim as the record of what was asked for.

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

---

## What landed, verified against a running instance 2026-07-31

**Field name:** `build_version`. **Type:** string. **Format:**
`"<app version>+<7-char commit SHA>"`, e.g. `"0.1.0+3f68be6"` — a raw SHA,
not an opaque hash, on the grounds that the repository is public and release
images are already tagged `sha-<full-sha>` in GHCR, so the revision is not a
secret and a raw SHA is directly debuggable. **Documented fallbacks:**
`"0.1.0+3f68be6.dirty"` for a dirty working tree, `"0.1.0+unknown"` when no
build metadata is available. It is a **required** property of
`HealthResponse` in `/openapi.json`, with a description and an example.

The contract version was bumped `1.0.0` → `1.1.0`, correctly, per the
application's own MINOR-is-additive policy.

### Acceptance criteria

| Criterion | Result |
|---|---|
| Field present alongside `status`, `schema_version`, `eval_tracing` | pass |
| Present in `/openapi.json` with its type | pass — required, described, with an example |
| Two consecutive calls return the same value | pass (5/5 identical) |
| Restart of the same build returns the same value | not separately provable while the value is the constant fallback |
| **Two different builds return different values** | **not satisfied on the deployed image** — see below |
| No git metadata yields the documented fallback rather than an error | pass — that is exactly what the deployed image returns |
| `/api/health` response time and failure behaviour unchanged | pass — 200 in ~10 ms |
| Existing consumers still work | pass — a full old-vs-new `/openapi.json` diff is *exactly* `+HealthResponse.build_version`; no path, schema, field or type removed or changed |

### The open item: the deployed image reports `0.1.0+unknown`

The application code is correct; the **deployment** is not passing the commit
into the image, so `build_version` is currently a constant. A constant is
precisely the failure this ask warned about — "a value that stays constant
across deploys is worse than absent, because it looks like a working signal."

Nothing in the application needs to change to fix this: the build that
produces the running container needs to supply the build metadata the code
already reads. Until it does, the field is present and honest but carries no
build identity.

**What this harness does about it:** `experiments/common.app_build()` maps any
value whose build metadata is `unknown` to the literal `"unavailable"`, rather
than recording a constant that looks like a fingerprint.
`compare_runs.py` keeps its "application changes may be invisible" warning for
exactly this case. Both revert to normal automatically once a real SHA is
served — no further harness change is needed.

### Knock-on effect the application team flagged, and its actual impact here

The `schema_version` bump to `1.1.0` was raised as a risk for any harness
pinned to an exact `1.0.0`. Confirmed and handled: the 14 notebooks compared
the `X-Schema-Version` header exact-match and printed a warning on **every
HTTP call** — noisy, never fatal. They and `experiments/common.py` now do a
minor-compatible check: same MAJOR and MINOR ≥ expected passes silently, a
MAJOR change raises, an application older than the goldens warns.

---

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

## State when this was raised, for reference

The instance as it was before the change, 2026-07-31:

```
GET /api/health   -> {"status":"ok","schema_version":"1.0.0","eval_tracing":true}
```

`HealthResponse` in `/openapi.json` had exactly three properties:
`status`, `schema_version`, `eval_tracing`. No build, release, or version
identifier was exposed anywhere in the OpenAPI surface. After the change:

```
GET /api/health   -> {"status":"ok","schema_version":"1.1.0","eval_tracing":true,
                      "build_version":"0.1.0+unknown"}
```

The fingerprint fields this ask supplements are served, and confirmed
working, on `GET /api/agent/trace/{run_id}`:

```
model:               "openai/gpt-4o-mini"
prompt_version:      "investigation_v1"
model_configuration: {"requested_model":"openai/gpt-4o-mini","temperature":0.0, ...}
```

## What this harness does when the field carries no identity

Per `CLAUDE.md`'s fail-loudly rule, the harness must never infer a build
identity it cannot read. The field now exists but reports its `+unknown`
fallback on the deployed image, so:

- `run_context()` records `app_build: "unavailable"` — the key is always
  present, never omitted, never guessed. A constant is not a fingerprint.
- `compare_runs.py` (Phase R2) prints a warning on any comparison where
  every run reports `"unavailable"`, stating that application changes may be
  invisible to attribution. It still compares — the measurement axes are
  unaffected — but it does not claim "no application change."

Both behaviours clear themselves as soon as the image is built with its
commit metadata; nothing further has to change here.

## Follow-ups

1. ~~Consume it in `run_context()` as an **application axis**~~ — done. It
   is in `APPLICATION_AXES`, so a change to it means the comparison is still
   valid and this is the explanatory variable ([`plan.md`](../../plan.md) §0).
2. **Open, application/ops side:** build the deployed image with its commit
   metadata so `build_version` stops reporting `+unknown`. Until then the
   warning in `compare_runs.py` stays, correctly.
3. ~~Record the agreed field name and fallback value~~ — done, above;
   [`plan.md`](../../plan.md) §11 item 3 updated.
4. Re-check `/openapi.json`, per `CLAUDE.md` — this repo is not notified
   when the application's surface changes. Last checked 2026-07-31: the diff
   against the previous build is exactly the one new field.
