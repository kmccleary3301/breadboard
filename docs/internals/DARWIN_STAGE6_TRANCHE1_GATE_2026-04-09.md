# DARWIN Stage 6 Tranche 1 gate

Date: `2026-04-09`

Gate criteria:

- live canary completes cleanly
- provider segmentation is canonical on live claim-bearing rows
- `single_family_lockout` comparisons are valid
- at least one positive activation probe exists
- no runtime-truth widening occurred

Decision:

- `PASS`

Evidence:

- live canary completed in:
  - `artifacts/darwin/stage6/tranche1/broader_transfer_canary/broader_transfer_canary_v0.json`
- provider segmentation is canonical:
  - `provider_segmentation_status = claim_rows_segmented`
  - `claim_rows_have_canonical_provider_segmentation = true`
- `single_family_lockout_comparison_count = 4`
- activation probe summary records one positive activation probe:
  - Repo_SWE topology challenge family
- the Systems-primary lockout adjustment stayed bounded:
  - no new lane
  - no new runtime surface
  - no transfer-matrix widening

Constraints carried forward:

- Systems remains the primary proving lane
- Repo_SWE remains the challenge lane
- the Systems family is still not positive under the current Tranche-1 settings
- Tranche 2 should therefore target broader transfer on the Systems-primary path first
