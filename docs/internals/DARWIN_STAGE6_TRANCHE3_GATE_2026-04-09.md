# DARWIN Stage 6 Tranche 3 gate

Date: `2026-04-09`

Gate criteria:

- broader-compounding series completes cleanly
- provider segmentation remains canonical
- at least one broader-compounding outcome is beyond flat/descriptive
- family registry exists
- broader-compounding scorecard exists
- replay posture is recorded
- no runtime-truth widening occurred

Decision:

- `PASS`

Evidence:

- broader-compounding series completed in:
  - `artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json`
- provider segmentation remains canonical through the retained-transfer base:
  - `provider_segmentation_status = claim_rows_segmented`
  - `claim_rows_have_canonical_provider_segmentation = true`
- broader-compounding outcomes are beyond flat/descriptive:
  - `positive_count = 2`
  - scheduling lane confidence class = `positive`
- family registry exists:
  - `artifacts/darwin/stage6/tranche3/family_registry/family_registry_v0.json`
- scorecard exists:
  - `artifacts/darwin/stage6/tranche3/scorecard/scorecard_v0.json`
- replay posture exists:
  - `artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json`
- the tranche stayed bounded:
  - no new lane beyond the current authorized set
  - no new runtime surface
  - no composition opening

Constraints carried forward:

- Systems remains the primary proving lane
- Repo_SWE remains the challenge lane
- the retained Systems-primary family center is now the final pre-composition baseline
- composition remains gated and should be addressed only in the final Stage-6 tranche
