# DARWIN External-Safe Evidence Policy V0

Date: 2026-03-19
Status: active internal policy for external-safe packaging
Scope: Phase-2 external-safe evidence tranche

## Purpose

This policy defines what DARWIN may package as external-safe evidence in the current tranche.

External-safe here means:

- safe for a technical reviewer outside the immediate DARWIN iteration loop
- bounded enough to avoid silent generalization
- explicit about replay, contamination, and cost-accounting limits

It does **not** mean public-launch-ready, frontier-grade, or superiority-safe.

## Allowed claim classes

### `operational_substrate`

Allowed examples:

- six current lanes emit shared DARWIN artifacts
- typed search and promotion-control surfaces are operational on the bounded lane set
- compiled execution/policy/evaluator/ledger surfaces exist and are replay-conditioned where required

### `reproducibility`

Allowed examples:

- the scoped artifact set can be rebuilt from named scripts
- replay-audited paths are explicitly identified
- canonical reviewer-facing artifacts are frozen for the tranche

### `bounded_comparative_clarity`

Allowed examples:

- repo_swe retained-baseline reasoning is clearer because invalid trials are preserved rather than flattened
- scheduling lineage reasoning is clearer because promotion depth and rollback targets are explicit
- compute-normalized views are bounded and caution-labeled

### `bounded_transfer_protocol`

Allowed examples:

- the prompt-family harness→research transfer is valid, replay-stable, and bounded
- transfer-family classification exists and preserves descriptive-only cases

## Prohibited claim classes

- superiority claims
- broad generalization claims
- model-ranking claims
- cross-benchmark dominance claims
- broad transfer-learning claims
- frontier-grade or publication-grade claims
- economic superiority claims from partial local-cost accounting

## Required caution labels

Every external-safe memo or packet in this tranche must carry the following labels when applicable:

- `internal_artifacts_repackaged_for_external_review`
- `bounded_lane_set`
- `additive_first`
- `replay_conditioned`
- `invalid_comparisons_disclosed`
- `local_cost_accounting_partial`
- `no_superiority_claim`

## Replay rule

A claim may enter the external-safe subset only if its replay dependence is explicit.

If replay is missing where the underlying path requires it:

- downgrade the claim to descriptive-only status, or
- exclude it from the external-safe subset

Do not widen scope merely to rescue a weak claim.

## Invalidity and contamination rule

No external-safe artifact may suppress:

- invalid-comparison rows
- budget mismatch caveats
- topology mismatch caveats
- evaluator comparability limits
- descriptive-only transfer status

If a reviewer would need this information to avoid overreading the result, it must travel with the packet.

## Cost-accounting rule

Any cost-facing comparative statement must preserve the existing DARWIN classification language:

- exact local zero
- exact local nonzero
- estimated local
- unavailable

No per-dollar comparison may be implied when the denominator is unavailable, zero, or only partially estimated.

## Canonical artifact rule

External-safe packaging must reference a frozen canonical artifact set.

Internal-only source material may still exist, but the memo and packet should cite the canonical set first and then point to supporting artifacts only when necessary.

## Boundary

- this policy does not authorize new runtime truth
- this policy does not authorize new lane expansion
- this policy does not authorize public-launch or superiority language
- this policy packages current DARWIN evidence; it does not expand DARWIN capability scope
