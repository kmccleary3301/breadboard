# ATP Hilbert Arm Audit Status — 2026-03-13

The program now distinguishes baseline and repaired BreadBoard evidence at the rollup level.

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/arm_audit_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/arm_audit_v1.md`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`

Current arm modes:

- `baseline_only`
- `focused_repaired`
- `split_rollup`
- `boundary_stress`

Current baseline-only pack:

- `pack_j_residue_gcd_mix_minif2f_v1`

Use the arm audit when interpreting aggregate results so one-shot comparator evidence is not conflated with focused theorem-local repair results.
