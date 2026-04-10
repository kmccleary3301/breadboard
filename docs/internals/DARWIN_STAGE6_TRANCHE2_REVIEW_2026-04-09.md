# DARWIN Stage 6 Tranche 2 review

Date: `2026-04-09`

Question set:

- is broader transfer now real on the Systems-primary path?
- is Repo_SWE still a useful challenge lane?
- do transfer outcomes now have typed retained / invalid / activation-probe meaning?
- is the failure surface informative enough to guide Tranche 3?
- is Stage 6 ready to leave “broader transfer becomes real” and move into broader compounding proof?

Findings:

- broader transfer is now real on the Systems-primary path:
  - `lane.systems -> lane.scheduling` is `retained`
  - target score improves from `0.600427` to `1.0`
  - replay status is `supported`
  - reflected in `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_outcome_summary_v1.json`
- Repo_SWE remains a useful challenge lane:
  - `lane.repo_swe -> lane.systems` remains `activation_probe`
  - the path still carries a positive Stage-6 activation signal without being overstated as retained transfer
- transfer outcomes are now typed and interpretable:
  - one `retained`
  - one `activation_probe`
  - one `invalid`
  - reflected in `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/transfer_cases_v2.json`
- the failure surface is informative enough to guide the next tranche:
  - current invalid transfer is not noise
  - it records a concrete Stage-6 rule boundary:
    - `inactive_family_not_authorized_for_stage6_transfer`
  - reflected in `artifacts/darwin/stage6/tranche2/broader_transfer_matrix/failed_transfer_taxonomy_v1.json`
- provider segmentation remains canonical on claim-bearing rows throughout the broader-transfer slice

Interpretation:

- Tranche 2 succeeded at its actual objective:
  - broader transfer is no longer just descriptive
  - the Systems-primary path now has one retained, replay-supported result
  - challenge-lane transfer remains legible without being overpromoted
- the retained transfer result is stronger than the source compounding result on the Systems family:
  - source remains `flat`
  - transfer target still improves materially
  - this is a real Stage-6 finding, not a contradiction
- the next Stage-6 question is no longer “can transfer be made real?”
- the next question is:
  - can broader compounding become real across the retained transfer surface without losing interpretability?

Conclusion:

- Tranche 2 is complete
- Stage 6 is ready to enter Tranche 3:
  - family-aware economics and broader compounding proof
- composition should remain gated until Tranche 3 earns it
