# DARWIN Transfer Family Policy V0

Date: 2026-03-19
Status: active internal tranche policy
Scope: Phase-2 transfer/lineage tranche

## Purpose

This document defines the bounded transfer-family set that DARWIN may use in the current transfer/lineage tranche.

The goal is not to generalize transfer broadly. The goal is to make transfer policy explicit enough that later lineage and evidence work have a stable internal substrate.

## In-scope transfer families

### `same_lane_operator_transfer`

Meaning:

- reuse an operator or policy pattern within the same lane across bounded candidate cycles

Examples:

- repo_swe topology or tool-scope reuse
- scheduling strategy-family reuse

### `cross_lane_prompt_family_transfer`

Meaning:

- reuse a prompt-family mutation or policy idea across lanes where evaluator semantics are still bounded enough for descriptive comparison

Examples:

- `lane.harness` prompt-tightening pattern applied to `lane.research`

### `cross_lane_policy_topology_transfer`

Meaning:

- reuse a topology or policy-family move across lanes when:
  - topology family is allowed in both lanes
  - budget class remains comparable
  - evaluator mismatch is explicitly reviewed

This family is descriptive-first and still bounded.

## Out of scope

- unconstrained many-to-many cross-lane transfer
- transfer across incomparable evaluator regimes without explicit invalidation
- transfer based on invalid trials
- public or superiority-style transfer claims
- runtime authorization derived from transfer results alone

## Validity classes

Every transfer result in this tranche must be classified as one of:

- `valid_improved`
- `valid_noop`
- `valid_degraded`
- `invalid_comparison`
- `replay_required_pending`

## Required recorded fields

Each transfer-family artifact or derived row should identify:

- transfer family id
- source lane id
- target lane id
- source candidate id
- target candidate id
- operator or policy family transferred
- budget class
- comparison-valid flag
- replay-required flag
- replay-stable flag if replay occurred
- validity reason or invalidation reason

## Replay policy

Replay is required before a transfer can inform downstream policy when:

- the result is `valid_improved`
- the transfer crosses lanes
- the transfer is intended to influence later promotion or lineage decisions

Replay is descriptive-only optional when:

- the result is `valid_noop`
- the result is `valid_degraded`
- the transfer is being used only for audit context

## Descriptive-only rule

Even a valid transfer remains descriptive-only unless:

- replay requirements are met
- invalid-comparison checks pass
- the tranche review explicitly allows the result to inform later policy

## Current proving posture

Primary proving lanes in this tranche:

- `lane.repo_swe`
- `lane.scheduling`

Bounded source/target transfer roles:

- `lane.harness`
- `lane.research`

Audit-only:

- `lane.atp`

## Boundary

- this policy is additive-first
- it does not authorize runtime-truth expansion
- it does not authorize broad transfer claims
- it does not supersede the invalid-comparison ledger
