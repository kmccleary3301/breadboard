# ATP Hilbert Pack K Status — 2026-03-13

## Scope

- Pack: `pack_k_moddigit_closedform_minif2f_v1`
- Family: bounded digit filters plus closed modular arithmetic
- Candidate arm: `bb_hilbert_like`
- Baseline arm: `hilbert_roselab`
- Arm mode: `baseline_only`

## Validity audit

- `mathd_numbertheory_728` is excluded as invalid.
- Reason: direct arithmetic gives `(29^13 - 5^13) % 7 = 3`, not `0`.
- Current valid task set:
  - `mathd_numbertheory_1124`
  - `mathd_numbertheory_293`
  - `mathd_numbertheory_328`
  - `mathd_numbertheory_175`
  - `mathd_numbertheory_769`

## Canonical artifacts

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/cross_system_validation_report_v1.json`
- BreadBoard results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/bb_hilbert_like_results_v2.jsonl`
- Hilbert results: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/hilbert_roselab_results_v1.jsonl`
- pack metadata: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_k_moddigit_closedform_minif2f_v1/pack_metadata.json`

## Result

- BreadBoard: `5/5`
- maintained Hilbert: `3/5`
- BreadBoard-only wins:
  - `mathd_numbertheory_1124`
  - `mathd_numbertheory_293`
- both solved:
  - `mathd_numbertheory_175`
  - `mathd_numbertheory_328`
  - `mathd_numbertheory_769`

## Spend

- maintained Hilbert exact spend on the valid slice:
  - input tokens: `37,167`
  - output tokens: `8,768`
  - estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.224438`
- BreadBoard estimated spend from the direct formal runner ledger:
  - prompt tokens: `1,110`
  - completion tokens: `383`
  - estimated cost: `~$0.008520`

## Read

- This is a cheap baseline-only tranche with no focused BreadBoard repair.
- BreadBoard is ahead on the valid slice and does not require theorem-local intervention here.
- Pack K satisfies the tenth-headline-pack requirement for the current Phase 1 completion gate.
