# DARWIN Stage 6 Tranche 2 gate

Date: `2026-04-09`

Gate criteria:

- broader-transfer rerun completes cleanly
- provider segmentation remains canonical
- at least one transfer outcome is beyond `descriptive_only`
- failed-transfer taxonomy exists
- transfer scorecard exists
- no runtime-truth widening occurred

Decision:

- `PASS`

Evidence:

- broader-transfer matrix completed in:
  - `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/broader_transfer_matrix_v0.json`
- provider segmentation remains canonical:
  - `provider_segmentation_status = claim_rows_segmented`
  - `claim_rows_have_canonical_provider_segmentation = true`
- transfer outcomes are now beyond `descriptive_only`:
  - `lane.systems -> lane.scheduling` = `retained`
  - `lane.repo_swe -> lane.systems` = `activation_probe`
- failed-transfer taxonomy exists:
  - `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/failed_transfer_taxonomy_v1.json`
- transfer scorecard exists:
  - `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_quality_scorecard_v1.json`
- the broader-transfer slice stayed bounded:
  - no new lane beyond the authorized `lane.scheduling` target
  - no new runtime surface
  - no composition opening

Constraints carried forward:

- Systems remains the primary proving lane
- Repo_SWE remains the challenge lane
- the retained transfer result should now be used as a base for Tranche 3 broader-compounding work
- composition remains gated
