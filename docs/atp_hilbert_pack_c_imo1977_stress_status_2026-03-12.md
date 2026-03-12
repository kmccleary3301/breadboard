# ATP Hilbert Pack C IMO 1977 P6 Stress Status — 2026-03-12

## Scope
- Pack: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_c_imo1977_p6_stress_minif2f_v1`
- Benchmark: `miniF2F`
- Task set: `imo_1977_p6`
- BreadBoard arm: `bb_hilbert_like`
- Hilbert arm: `hilbert_roselab`
- Model: OpenRouter `openai/gpt-5.4`

## Results
- BreadBoard: `0/1` solved
- Maintained Hilbert: `0/1` solved
- Paired report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_c_imo1977_p6_stress_minif2f_v1/cross_system_pilot_report_v1.json`
- Validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_c_imo1977_p6_stress_minif2f_v1/cross_system_validation_report_v1.json`

## Interpretation
- Both systems produced structured but invalid attempts.
- This tranche remains a stress-only comparison, not a calibration lane.
- The bounded Hilbert settings prevented the earlier open-ended spend failure mode.

## Hilbert Spend
- Proof stats: `artifacts/benchmarks/hilbert_runs/pack_c_imo1977_p6_stress_gpt54_roselab_v1/proofs/proof_stats/imo_1977_p6_stats.json`
- Input tokens: `18,350`
- Output tokens: `7,262`
- Estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `$0.154805`

## Next Step
- Keep `imo_1977_p6` isolated as a stress pack.
- Use the valid Pack B core-noimo lane for comparative progress, and only revisit this pack with explicit spend approval.
