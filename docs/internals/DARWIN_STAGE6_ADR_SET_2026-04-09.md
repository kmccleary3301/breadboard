# DARWIN Stage 6 ADR set

Date: `2026-04-09`

This is the minimum ADR surface worth freezing before Stage-6 code work starts.

## ADR-Stage6-01 — thesis and anti-goals

Freeze:

- broader-transfer and family-activation thesis
- no lane-expansion-first
- no composition-first
- no runtime-truth rewrite

## ADR-Stage6-02 — `FamilyActivationV1`

Freeze:

- activation states
- lane scope
- campaign scope
- comparison-mode scope
- activation and demotion reasons
- next-review triggers

## ADR-Stage6-03 — `ComparisonEnvelopeV1`

Freeze:

- lane id
- evaluator-pack version
- support-envelope digest
- execution mode
- route class
- provider origin
- budget class
- comparison class
- cache-normalization mode

## ADR-Stage6-04 — `TransferCaseV2`

Freeze:

- transfer-case record shape
- retained / non-retained / invalid / descriptive-only outcomes
- bounded matrix doctrine
- transfer eligibility rules

## ADR-Stage6-05 — `SearchPolicyV2` refinement

Freeze:

- activation-aware inputs
- `single_family_lockout`
- `delayed_activation`
- bounded `activation_probe`
- route/provider segmentation carryover

## ADR-Stage6-06 — lane matrix and breadth boundaries

Freeze:

- Systems primary
- Repo_SWE challenge
- Scheduling transfer confirmation
- Harness watchdog
- ATP audit-only
- Research closed unless explicitly authorized later

## ADR-Stage6-07 — composition gate

Freeze:

- composition remains closed by default
- preconditions for a composition canary
- explicit non-authorization remains a valid outcome
