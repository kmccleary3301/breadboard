# ATP Hilbert Replay Audit Status — 2026-03-13

## Scope

- Replay audit packs:
  - `pack_k_moddigit_closedform_minif2f_v1` (`easy`)
  - `pack_l_algebra_linear_equiv_minif2f_v1` (`medium`)
- Candidate arm: `bb_hilbert_like`
- Baseline arm: `hilbert_roselab`
- Summary artifact: `artifacts/benchmarks/hilbert_replay_audit_v1/replay_audit_summary_v1.json`

## Replay artifacts

### Pack K replay

- report: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_k_moddigit_closedform_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_k_moddigit_closedform_minif2f_v1/cross_system_validation_report_v1.json`
- BreadBoard replay rows: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_k_moddigit_closedform_minif2f_v1/bb_hilbert_like_results_v1.jsonl`
- Hilbert replay rows: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_k_moddigit_closedform_minif2f_v1/hilbert_roselab_results_v1.jsonl`

### Pack L replay

- report: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_validation_report_v1.json`
- BreadBoard replay rows: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_l_algebra_linear_equiv_minif2f_v1/bb_hilbert_like_results_v1.jsonl`
- Hilbert replay rows: `artifacts/benchmarks/hilbert_replay_audit_v1/pack_l_algebra_linear_equiv_minif2f_v1/hilbert_roselab_results_v1.jsonl`

## Result

### Pack K

- canonical: BreadBoard `5/5`, Hilbert `3/5`
- replay: BreadBoard `5/5`, Hilbert `4/5`
- candidate count drift: none
- baseline drift:
  - `mathd_numbertheory_1124` flipped from `UNSOLVED` to `SOLVED`
- replay Hilbert spend:
  - input tokens: `18,689`
  - output tokens: `3,828`
  - estimated cost: `~$0.104142`

### Pack L

- canonical: BreadBoard `6/6`, Hilbert `3/6`
- replay: BreadBoard `6/6`, Hilbert `4/6`
- candidate count drift: none
- baseline drift:
  - `mathd_algebra_33` flipped from `UNSOLVED` to `SOLVED`
- replay Hilbert spend:
  - input tokens: `34,936`
  - output tokens: `8,584`
  - estimated cost: `~$0.216100`

## Read

- The replay audit requirement is met.
- BreadBoard is stable across both replayed canonical packs: solve counts did not move on either the easy or medium slice.
- Maintained Hilbert improved by exactly one task on both replayed packs.
- This is real baseline drift, not a validator artifact, because the paired replay reports are fully valid and the drift is isolated to explicit task-level flips.
- The program-level interpretation does not change: BreadBoard still leads both replayed packs after replay.

## Practical conclusion

- Gate F should now be treated as `met`.
- Future aggregate claims should acknowledge that maintained-Hilbert replay variance exists even under fixed bounded settings.
- BreadBoard's current advantage is robust to this replay audit.
