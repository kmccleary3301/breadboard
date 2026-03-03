# Governance V2 Policy (ACR/ADR/XP)

Date: 2026-02-24

## Purpose

Provide explicit, low-ambiguity decision policy for P3 execution so capability work scales without kernel narrowing.

## Decision Artifacts

- ACR template: `docs/contracts/policies/ARCHITECTURE_CHANGE_REQUEST_TEMPLATE_V1.md`
- ADR template: `docs/contracts/policies/ARCHITECTURE_DECISION_RECORD_TEMPLATE_V1.md`
- XP template: `docs/contracts/policies/EXPERIMENT_PROPOSAL_TEMPLATE_V1.md`

## Required Use Conditions

### ACR (Architecture Change Request) — required when:

1. Any kernel danger-zone contract file is modified.
2. Any parity-critical request or tool schema baseline is intentionally changed.
3. Any replay/evidence protocol invariants are changed.

### ADR (Architecture Decision Record) — required when:

1. Extension architecture is changed in a way that affects multiple subsystems.
2. Runtime behavior policy changes materially (e.g., fallback ordering, diagnostic class semantics).
3. A non-danger-zone structural decision needs durable rationale.

### XP (Experiment Proposal) — required when:

1. A capability claim is intended from an experiment run.
2. Bench/ablation experiments are added or materially changed.
3. Budgeted live spend is used for ATP/EvoLake/C-Trees research iterations.

## Merge Gate Mapping

| Change class | Required artifacts | Required checks |
|---|---|---|
| Kernel danger-zone contract change | ACR (+ ADR if architecture rationale needed) | `check_danger_zone_acr.py`, kernel contract pack hash check, replay/conformance evidence |
| ATP contract schema/validator change | ACR + ADR | `check_atp_contract_governance.py`, ATP contract validation, benchmark/evidence checks |
| Extension architecture/policy change (non-danger-zone) | ADR | extension-specific tests + relevant conformance checks |
| Capability experiment / claim-producing run | XP + claim ledger update | evidence bundle validation + falsification/ablation evidence |
| Spend-bearing live run tied to evidence | XP + spend attribution fields | spend guard + spend attribution digest inclusion |

## Merge-Time Checklist Binding

PR template must reference:

- ACR template
- ADR template
- XP template
- rollback checklist
- ATP contract break checklist (when ATP schema/validator changes)

## Escalation

If one change spans multiple classes, apply the strictest artifact set:

- `danger-zone > contract > architecture > experiment`.
