# DARWIN Stage 6 tranche-3 status

Date: `2026-04-09`

Scope landed in this slice:

- Stage-6 broader compounding was operationalized over the retained Systems-primary transfer base in:
  - `breadboard_ext/darwin/stage6.py`
  - `scripts/run_darwin_stage6_broader_compounding_v0.py`
- Stage-6 now emits:
  - compounding-rate report
  - family registry
  - economics attribution
  - transfer-to-compounding linkage
  - replay posture
  - scorecard
  - verification bundle

Current read:

- the broader-compounding run now emits:
  - `2` broader-compounding rows
  - `2` positive broader-compounding outcomes
  - `0` flat outcomes
  - family-center decision = `hold_single_retained_family_center`
- the retained Systems-primary path now compounds positively on the bounded scheduling target:
  - score lift vs `family_lockout` = `0.218337`
  - score lift vs `single_family_lockout` = `0.399573`
  - confidence class = `positive`
  - trend = `stable_positive`
- provider segmentation remains canonical through the retained-transfer base:
  - `provider_segmentation_status = claim_rows_segmented`
  - `claim_rows_have_canonical_provider_segmentation = true`
- transfer-to-compounding linkage is now explicit:
  - `lane.systems -> lane.scheduling` = `retained_transfer_compounds`
- replay posture is now explicit:
  - retained transfer = `supported`
  - scheduling retained-family broader compounding = `supported`

Artifacts:

- `artifacts/darwin/stage6/tranche3/broader_compounding/broader_compounding_v0.json`
- `artifacts/darwin/stage6/tranche3/compounding_rate/compounding_rate_v0.json`
- `artifacts/darwin/stage6/tranche3/family_registry/family_registry_v0.json`
- `artifacts/darwin/stage6/tranche3/economics_attribution/economics_attribution_v0.json`
- `artifacts/darwin/stage6/tranche3/transfer_compounding_linkage/transfer_compounding_linkage_v0.json`
- `artifacts/darwin/stage6/tranche3/replay_posture/replay_posture_v0.json`
- `artifacts/darwin/stage6/tranche3/scorecard/scorecard_v0.json`
- `artifacts/darwin/stage6/tranche3/verification_bundle/verification_bundle_v0.json`

What is complete in this slice:

- broader compounding is now real on top of the retained Systems-primary transfer
- the family center is now explicit and stable enough for the tranche:
  - `hold_single_retained_family_center`
- economics and comparison surfaces are now reviewer-legible
- replay posture exists for both the retained transfer and the strongest broader-compounding result
- Repo_SWE remains a bounded challenge context without destabilizing the proving center

What is not yet complete:

- composition remains gated and untested
- broader compounding is still bounded to the current lane set and retained-family center
- final Stage-6 comparative / closeout work is still ahead
