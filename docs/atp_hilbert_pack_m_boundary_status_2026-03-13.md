# ATP Hilbert Pack M Boundary Status — 2026-03-13

## Scope

- Pack: `pack_m_boundary_olympiad_mix_minif2f_v1`
- Family: harder olympiad / induction / divisibility boundary slice
- Candidate arm: `bb_hilbert_like`
- Baseline arm: `hilbert_roselab`
- Arm mode: `boundary_stress`

## Validity audit

- No invalid extracted statements are currently excluded from Pack M.
- Canonicalization required for this tranche:
  - `real.` → `Real.`
  - lowercase `finset` namespaces already normalize through the tranche builder
- Current valid task set:
  - `imo_1960_p2`
  - `imo_1963_p5`
  - `induction_nfactltnexpnm1ngt3`
  - `numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown`

## Canonical artifacts

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/cross_system_validation_report_v1.json`
- BreadBoard results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/bb_hilbert_like_results_v1.jsonl`
- Hilbert results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/hilbert_roselab_results_v1.jsonl`
- pack metadata: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_m_boundary_olympiad_mix_minif2f_v1/pack_metadata.json`

## Result

- BreadBoard: `0/4`
- maintained Hilbert: `0/4`
- both unsolved:
  - `imo_1960_p2`
  - `imo_1963_p5`
  - `induction_nfactltnexpnm1ngt3`
  - `numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown`

## Spend

- maintained Hilbert exact spend on the valid slice:
  - input tokens: `102,693`
  - output tokens: `39,092`
  - estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.843113`
- BreadBoard estimated spend from the direct formal runner ledger:
  - prompt tokens: `717`
  - completion tokens: `3,747`
  - estimated cost: `~$0.057998`

## Read

- This is a genuine boundary-stress tranche, not a hygiene failure.
- Both systems are fully covered and both fail all four tasks under the bounded caps.
- Pack M is useful because it locates a harder region where the current BreadBoard advantage over maintained Hilbert disappears rather than widens.
