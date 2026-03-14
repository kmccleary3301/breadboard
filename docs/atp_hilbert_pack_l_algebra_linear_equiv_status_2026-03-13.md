# ATP Hilbert Pack L Status — 2026-03-13

## Scope

- Pack: `pack_l_algebra_linear_equiv_minif2f_v1`
- Family: linear algebraic equalities plus equivalence/inverse rewrites
- Candidate arm: `bb_hilbert_like`
- Baseline arm: `hilbert_roselab`
- Arm mode: `baseline_only`

## Validity audit

- No invalid extracted statements are currently excluded from Pack L.
- Canonicalization required for this tranche:
  - `equiv` → `Equiv`
  - `.denom` → `.den`
- Current valid task set:
  - `mathd_algebra_141`
  - `mathd_algebra_209`
  - `mathd_algebra_33`
  - `mathd_algebra_398`
  - `mathd_algebra_459`
  - `mathd_algebra_137`

## Canonical artifacts

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/cross_system_validation_report_v1.json`
- BreadBoard results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/bb_hilbert_like_results_v3.jsonl`
- Hilbert results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/hilbert_roselab_results_v2.jsonl`
- pack metadata: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_l_algebra_linear_equiv_minif2f_v1/pack_metadata.json`

## Result

- BreadBoard: `6/6`
- maintained Hilbert: `3/6`
- BreadBoard-only wins:
  - `mathd_algebra_209`
  - `mathd_algebra_33`
  - `mathd_algebra_398`
- both solved:
  - `mathd_algebra_141`
  - `mathd_algebra_459`
  - `mathd_algebra_137`

## Spend

- maintained Hilbert exact spend on the valid slice:
  - input tokens: `46,004`
  - output tokens: `10,761`
  - estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.276425`
- BreadBoard estimated spend from the direct formal runner ledger:
  - prompt tokens: `1,616`
  - completion tokens: `843`
  - estimated cost: `~$0.016685`

## Read

- This is a clean baseline-only tranche with no focused BreadBoard repair.
- BreadBoard saturated the slice after canonicalization and one theorem-local route correction for `mathd_algebra_209`.
- Pack L extends the headline tranche family beyond arithmetic/modular slices and preserves BreadBoard's advantage against the maintained Hilbert fork.
