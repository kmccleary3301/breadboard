# ATP Hilbert Canonical Baselines — 2026-03-13

This note locks the currently completed ATP/Hilbert comparison tranches so the next phase can build aggregate scoreboards, tranche-selection policy, spend backfill, and new packs without re-litigating which reports are canonical.

Canonical machine-readable index:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`

Canonical human-readable index:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.md`

Locked tranche families:

- supporting filtered lane: `pack_b_medium_noimo530_minif2f_v1`
- primary Pack B lane: `pack_b_core_noimo_minif2f_v1`
- stress boundary lane: `pack_c_imo1977_p6_stress_minif2f_v1`
- Pack D support splits:
  - `pack_d_induction_core_minif2f_v1`
  - `pack_d_numbertheory_core_minif2f_v1`
- Pack D canonical roll-up: `pack_d_mixed_induction_numbertheory_minif2f_v1`
- Pack E support focus: `pack_e_algebra_focus_minif2f_v1`
- Pack E canonical core: `pack_e_algebra_core_minif2f_v1`
- Pack F canonical merged rowset: `pack_f_discrete_arithmetic_mix_minif2f_v1`
- Pack G canonical core: `pack_g_arithmetic_sanity_minif2f_v1`
- Pack H canonical core: `pack_h_modular_closedform_minif2f_v1`
- Pack I canonical core: `pack_i_divisors_modmix_minif2f_v1`
- Pack J canonical core: `pack_j_residue_gcd_mix_minif2f_v1`

Interpretation rules:

- Use the JSON index as the source of truth for canonical report and validation paths.
- Treat older report versions outside the index as historical only.
- Treat `supporting_focus` and `supporting_split` entries as admissible evidence for roll-up/context, but not as replacements for the primary pack families unless explicitly noted in the status doc.
- Treat the Pack C stress lane as a boundary tranche, not a calibration/comparator lane.
