# DARWIN Phase-2 Transfer/Lineage Tranche Review

Date: 2026-03-19
Status: tranche review complete
Primary issue: `breadboard_repo_darwin_phase1_20260313-xrz`

## Review result

The current transfer/lineage tranche is coherent for its scoped purpose.

Primary conclusions:

- transfer-family policy is now explicit and bounded
- lineage / rollback / supersession policy is explicit enough for derived multi-tranche reasoning
- repo_swe is now a credible invalid-comparison-sensitive proving lane
- scheduling is now a credible multi-cycle lineage proving lane
- harness and research remain bounded transfer source/target roles
- ATP remains audit-only

## Evidence considered

- `docs/contracts/darwin/DARWIN_TRANSFER_FAMILY_POLICY_V0.md`
- `docs/contracts/darwin/DARWIN_LINEAGE_POLICY_V0.md`
- `artifacts/darwin/search/evolution_ledger_v0.json`
- `artifacts/darwin/reviews/transfer_family_view_v0.json`
- `artifacts/darwin/reviews/lineage_review_v0.json`
- `artifacts/darwin/reviews/transfer_lineage_proving_review_v0.json`

## Review notes by lane

### `lane.repo_swe`

- retained baseline decision is now explicit
- invalid-trial pressure is separated from replay-stable rejected candidates
- the lane is suitable as the primary transfer-sensitive proving lane

### `lane.scheduling`

- promotion depth and supersession chain are explicit
- rollback target remains visible in the derived lineage review
- the lane is suitable as the primary lineage proving lane

### `lane.harness` and `lane.research`

- bounded prompt-family transfer remains the only valid transfer example
- it stays internal and bounded rather than generalized

### `lane.atp`

- ATP remains audit-only
- nothing in this tranche justifies moving ATP into the transfer-family proving center

## Decision

The transfer/lineage tranche is complete for its current bounded purpose.

Next-tranche recommendation:

- activate external-safe evidence packaging as the next scoped tranche

Still blocked:

- no runtime-truth expansion is authorized by this review alone
- no public or superiority-style transfer claim is authorized
