# DARWIN Stage 6 tranche-2 status

Date: `2026-04-09`

Scope landed in this slice:

- Stage-6 broader transfer was operationalized beyond the canary in:
  - `breadboard_ext/darwin/stage6.py`
  - `scripts/run_darwin_stage6_broader_transfer_matrix_v0.py`
- `TransferCaseV2` now supports typed Stage-6 transfer outcomes:
  - `retained`
  - `degraded_but_valid`
  - `activation_probe`
  - `descriptive_only`
  - `invalid`
  - `inconclusive`
- Stage-6 now emits:
  - transfer outcome summary
  - failed-transfer taxonomy
  - transfer quality scorecard
  - activation-to-transfer linkage

Current read:

- the broader-transfer matrix now emits:
  - `3` transfer cases
  - `1` retained transfer
  - `1` non-retained challenge transfer
  - `1` invalid transfer
- provider/route segmentation remains canonical on claim-bearing rows:
  - `provider_segmentation_status = claim_rows_segmented`
  - `claim_rows_have_canonical_provider_segmentation = true`
- the Systems-primary path now records a retained transfer:
  - `lane.systems -> lane.scheduling`
  - family: `component_family.stage4.policy.policy.shadow_memory_enable_v1.lane.systems.v0`
  - status: `retained`
  - rationale: `target_lane_score_improved_materially`
  - target baseline score: `0.600427`
  - target transferred score: `1.0`
  - replay status: `supported`
- the Repo_SWE challenge path remains useful:
  - `lane.repo_swe -> lane.systems`
  - status: bounded challenge context
  - source conclusion: non-retained and reviewer-legible
  - replay status: `observed`
- the held-back Repo_SWE tool-scope family now produces an explicit invalid transfer row:
  - status: `invalid`
  - reason: `inactive_family_not_authorized_for_stage6_transfer`

Artifacts:

- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/broader_transfer_matrix_v0.json`
- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_cases_v2.json`
- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_outcome_summary_v1.json`
- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/failed_transfer_taxonomy_v1.json`
- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_quality_scorecard_v1.json`
- `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/activation_transfer_linkage_v1.json`

What is complete in this slice:

- broader transfer is now real on the Systems-primary path
- transfer outcomes are typed and reviewer-legible
- failed-transfer taxonomy is grounded in actual outcomes
- replay/confirmation exists for the strongest retained transfer
- provider segmentation did not regress during broader transfer work

What is not yet complete:

- broader transfer is not yet broad in the Stage-6 sense; it is still bounded to the current lane set
- the Systems source family remains `flat` as a compounding source even though the transfer result is retained
- composition is still gated and out of scope for this tranche
