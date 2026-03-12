# ATP Hilbert Pack D Mixed Induction/Number Theory Status — 2026-03-12

## Scope
- Pack: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1`
- Benchmark: `miniF2F`
- BreadBoard arm: `bb_hilbert_like`
- Hilbert arm: `hilbert_roselab`
- Model: OpenRouter `openai/gpt-5.4`

Tasks:
- `imo_1959_p1`
- `induction_sumkexp3eqsumksq`
- `induction_12dvd4expnp1p20`
- `numbertheory_2pownm1prime_nprime`
- `mathd_numbertheory_427`
- `mathd_algebra_452`

## Initial v1 result
- BreadBoard: `0/6`
- Maintained Hilbert: `1/6`
- Hilbert-only: `mathd_algebra_452`

Reference artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_pilot_report_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_validation_report_v1.json`

## Root cause found after v1
- Pack D still contained legacy Lean 3 theorem syntax in extracted statements.
- Fixed in the builder:
  - namespace normalization: `nat.* -> Nat.*`, `finset.* -> Finset.*`
  - lambda normalization: `λ x, ... -> fun x => ...`
- This is a real input-normalization issue, not a theorem-strategy issue.

## Current normalized state
- BreadBoard focused rerun flipped `mathd_algebra_452` to `SOLVED`.
- Full BreadBoard rerun on normalized inputs is now `2/6`:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/bb_hilbert_like_slice_summary_v3.json`
- The first normalized Hilbert rerun was stopped after the remaining lambda defect was identified in-task, to avoid wasting more spend on stale inputs.

## Final corrected rerun
- BreadBoard corrected rerun:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/bb_hilbert_like_slice_summary_v4.json`
  - result: `2/6`
- Maintained-Hilbert corrected rerun:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/hilbert_roselab_summary_v2.json`
  - result: `1/6`
- Paired artifacts:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_validation_report_v3.json`
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_mixed_induction_numbertheory_minif2f_v1/cross_system_pilot_report_v3.json`

Per-task result on corrected inputs:
- `imo_1959_p1` — BreadBoard only
- `mathd_algebra_452` — both solved
- `induction_sumkexp3eqsumksq` — both unsolved
- `induction_12dvd4expnp1p20` — both unsolved
- `numbertheory_2pownm1prime_nprime` — both unsolved
- `mathd_numbertheory_427` — both unsolved

## Spend
- Initial Hilbert v1 exact telemetry:
  - input tokens: `70,824`
  - output tokens: `13,034`
  - estimated cost: `~$0.372570`
- Final corrected Hilbert v3 exact telemetry:
  - input tokens: `59,102`
  - output tokens: `17,699`
  - estimated cost: `~$0.413240`
- The partial normalized Hilbert rerun was intentionally cut short and should not be treated as a finalized benchmark artifact.

## Next Step
- Pack D is now finalized on corrected inputs.
- The next useful move is to split the remaining four shared-unsolved tasks into narrower follow-up work:
  - induction-focused subpack: `induction_sumkexp3eqsumksq`, `induction_12dvd4expnp1p20`
  - number-theory-focused subpack: `numbertheory_2pownm1prime_nprime`, `mathd_numbertheory_427`
- Do not spend more on the full mixed six-task tranche; it is now informative enough.
