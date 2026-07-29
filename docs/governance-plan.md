# Governance — verifying the application's own audit trail from outside

**Status: design document. Nothing here has been installed or executed.**
`observability-plan.md` §3.2 and §5 already settled two governance
questions — no Langfuse prompt management for application prompts, and the
PII stance on any data leaving this machine. Don't re-litigate either here.
This doc is about a different, newer finding: the application already ships
its own audit/governance features, and this repo's job is to verify them
from outside, not to design them.

## 1. What the application already has, that this repo doesn't yet test

Confirmed against a running instance's `/openapi.json` (2026-07-29):

| Feature | Endpoint | What it implies |
|---|---|---|
| Decisions require a human, an action, and a reason | `POST /api/cases/{id}/decisions` — `action` (`approve`/`reject`/`escalate`/`request_evidence`), `analyst_id`, `rationale` (all required, `rationale` non-empty), optional `agent_run_id` | The application already enforces that no decision is recorded without attribution and a reason. That's a governance control already built — this repo's job is to confirm it's actually enforced, not to add a second one |
| Decisions can reference the agent run that informed them | `agent_run_id` on `DecisionRequest` | A decision is traceable back to a specific investigation, which is traceable to a specific `model` / `prompt_version` via `GET /api/agent/trace/{run_id}` |
| Status changes are attributed | `PATCH /api/cases/{id}/status` — `actor_id` required | Same pattern as decisions: no anonymous state change |
| A persisted, stable audit artifact exists | `GET /api/reports/eod` — described as *"every decision in the period with its rationale, linked evidence, and informing agent run, plus AI-usage and tool-call aggregates... persisted to audit_reports as a stable artifact"* | This is the application's own compliance report. It already exists. Nothing here should propose building a competing one |
| Every run carries a version fingerprint | `GET /api/agent/trace/{run_id}` — `model`, `model_configuration`, `prompt_version` | Every recommendation is, in principle, traceable to the exact model and prompt version that produced it — the traceability a regulated decision system needs |
| No decision-edit or decision-delete endpoint is documented | Absent from `/openapi.json` | Decisions appear append-only by design. See §4 for what this repo can and can't claim about that |

None of this needed to be built — it's already there. What's missing is a
black-box test that actually exercises it and would catch it breaking.

## 2. What to build, ordered by value

### Phase 0 — Role-boundary enforcement (no application change, cheap, do first)

`README.md` already documents three API-key roles (`analyst`, `eval_reader`,
`test_operator`) and which endpoints need which. That's an access-control
claim nothing currently tests. Using the per-role env vars
(`AML_API_KEY_EVAL_READER`, etc.) already scaffolded in `.env.example`,
attempt each sensitive call with the *wrong* role's key and assert `403`,
not just the happy path with the right key. Concretely: `eval_reader` key
against `POST /api/dev/reset`, `analyst` key against `GET
/api/agent/trace/{run_id}`, and so on.

This is a real access-control test, entirely black-box, needs no
application change, and is currently untested — the suite exercises the
*right* key for each call and never checks that the *wrong* one is refused.

### Phase 1 — Decision/rationale enforcement, as an assertion against the documented contract

Confirm the constraints `DecisionRequest`/`StatusRequest` already declare are
actually enforced: a decision with empty `rationale` or missing `action`
returns `422`, not a silently-accepted decision. This is testing that a
governance control the application already claims to have actually holds —
the same spirit as the rest of this suite (verify the documented contract,
don't assume it).

### Phase 2 — End-of-day report stability

The EOD report is described as persisted and stable for a closed period.
Test that: call `GET /api/reports/eod?date=<a past, closed date>` twice and
assert the two responses match. If a report for a closed date differs
between calls, that's a real governance defect — an audit artifact that
regenerates differently after the fact is close to worthless as an audit
artifact. This needs no application change; it's a black-box consistency
check of a feature that already claims stability.

### Phase 3 — Traceability completeness

For every investigation this suite already runs, assert
`GET /api/agent/trace/{run_id}` returns non-null `model` and
`prompt_version` (both are declared required in the schema, so this checks
the contract is honored in practice, not just declared). This is a one-line
addition to notebooks that already call this endpoint, not a new pipeline —
folded in here because it's a governance property (every recommendation
must be attributable to a specific model/prompt version), not a
correctness one.

## 3. What's the application's job, not this repo's

- **Deciding retention, legal hold, or deletion policy** for decisions,
  reports, or traces. This repo can test that the *mechanism* it observes
  behaves consistently; it cannot and should not decide *policy* — that's a
  compliance-function decision, the same way `observability-plan.md` §5
  defers the PII residency call to whoever owns data protection.
- **Building a second audit report.** `/api/reports/eod` already exists and
  is described as the system of record. Testing it is this repo's job;
  duplicating it is not.
- **Enforcing access control.** Phase 0 above *tests* that role boundaries
  hold; it does not implement them. If Phase 0 finds a role boundary that
  doesn't hold, the fix is an application change, reported the same way
  `metric-notes.md` reports `PlanAdherenceMetric`'s blocker — named
  precisely, not designed from here.

## 4. What not to do

### Don't claim to have "verified immutability" from a black-box test

The absence of a documented edit/delete endpoint for decisions is evidence
of intent, observable from outside. It is not a cryptographic or
database-level immutability guarantee — a black-box test can't see whether
a row can still be updated directly in the database, only that the
documented API surface doesn't offer a way to. Report Phase 0-style findings
as exactly that: "no API-level path to alter a recorded decision was found,"
not "decisions are proven immutable."

### Don't let this repo's own persisted history become an ungoverned copy of sensitive data

`regression-testing-plan.md` and `performance-latency-plan.md` both propose
local history files (scores, latencies, run metadata). If any of that
history captures `actual_output`, decision `rationale` text, or anything
else that traces back to a real customer, it is subject to the same
standard `observability-plan.md` §5 sets for Langfuse — the fact that it's a
local file instead of a third-party service doesn't make it automatically
safe. If those history mechanisms are ever pointed at an instance carrying
real customer-derived data, decide retention and access for that local
store deliberately, not by default.

### Don't re-open the prompt-management or PII questions here

Both are already decided, with reasoning, in `observability-plan.md`. A
governance doc that quietly re-argues a settled question is worse than one
that just links to it.

## 5. Honest summary

**Genuinely valuable, cheap, do first:** role-boundary testing (Phase 0) —
the three-role access model is documented and currently unverified in
either direction; decision/rationale contract enforcement (Phase 1); EOD
report stability (Phase 2) — all black-box, all using endpoints that already
exist, none needing an application change.

**Valuable, nearly free once you're already calling the endpoint:**
traceability completeness (Phase 3) — one assertion added to existing
notebook flow.

**Not this repo's job, regardless of effort available:** deciding retention
or legal-hold policy; building a second audit report next to
`/api/reports/eod`; claiming cryptographic or database-level guarantees a
black-box test cannot see.

**Depends on a decision only you can make:** whether Phase 0's role-boundary
failures (if any turn up) get reported and fixed upstream on the timeline
you'd want for a compliance-relevant access-control gap, or whether they sit
in a backlog like `PlanAdherenceMetric` has. That's a severity call, not a
technical one.
