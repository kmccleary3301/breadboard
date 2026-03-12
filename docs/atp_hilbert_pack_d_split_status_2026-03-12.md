# ATP Hilbert Pack D Split Follow-Up Status — 2026-03-12

## Scope
- Parent tranche: `pack_d_mixed_induction_numbertheory_minif2f_v1`
- Follow-up subpacks:
  - `pack_d_induction_core_minif2f_v1`
  - `pack_d_numbertheory_core_minif2f_v1`
- Benchmark: `miniF2F`
- BreadBoard arm: `bb_hilbert_like`
- Hilbert arm: `hilbert_roselab`
- Model: OpenRouter `openai/gpt-5.4`

## Pack D induction core
Tasks:
- `induction_sumkexp3eqsumksq`
- `induction_12dvd4expnp1p20`

Artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_induction_core_minif2f_v1/cross_system_pilot_report_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_induction_core_minif2f_v1/cross_system_validation_report_v1.json`

Outcome:
- BreadBoard: `0/2`
- Maintained Hilbert: `0/2`
- Shared unsolved: both tasks

Hilbert exact telemetry:
- input tokens: `26,663`
- output tokens: `8,191`
- estimated cost: `~$0.189522`

Read:
- This subpack is low-yield at the current bounded settings.
- Both systems fail cleanly, so the next move here should be theorem-local induction scaffolds rather than more pack-scale reruns.

## Pack D number theory core
Tasks:
- `imo_1959_p1`
- `numbertheory_2pownm1prime_nprime`
- `mathd_numbertheory_427`
- `mathd_algebra_452`

Artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_numbertheory_core_minif2f_v1/cross_system_pilot_report_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_d_numbertheory_core_minif2f_v1/cross_system_validation_report_v1.json`

Outcome:
- BreadBoard: `2/4`
- Maintained Hilbert: `1/4`
- BreadBoard-only: `imo_1959_p1`
- Both solved: `mathd_algebra_452`
- Shared unsolved:
  - `numbertheory_2pownm1prime_nprime`
  - `mathd_numbertheory_427`

Hilbert exact telemetry:
- input tokens: `57,691`
- output tokens: `13,182`
- estimated cost: `~$0.341958`

Read:
- The number-theory split retains the signal from the parent mixed tranche.
- BreadBoard keeps the edge on the gcd-style theorem `imo_1959_p1`.
- The remaining work is now concentrated on:
  - Mersenne-prime-to-prime-exponent theorem routing
  - divisors/filter arithmetic normalization for `mathd_numbertheory_427`

## Next Step
- Keep the split packs and stop spending on the mixed six-task Pack D tranche.
- Next focused ATP work should prioritize the number-theory core pack, specifically:
  - `numbertheory_2pownm1prime_nprime`
  - `mathd_numbertheory_427`
- Only revisit the induction subpack after adding theorem-local guidance, because current pack-scale reruns there are not differentiating either system.
