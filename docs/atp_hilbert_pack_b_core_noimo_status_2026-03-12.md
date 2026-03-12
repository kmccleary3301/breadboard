# ATP Hilbert Pack B Core No-IMO Status — 2026-03-12

This note records the next valid comparison tranche after the filtered Pack B medium slice. The core-noimo pack keeps the valid MiniF2F tasks that still matter for ATP comparison and excludes the previously removed invalid or cost-skewed cases.

## Pack

Path:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/`

Tasks:
- `mathd_numbertheory_530`
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- `mathd_algebra_156`
- `mathd_algebra_171`
- `numbertheory_2dvd4expn`
- `mathd_algebra_107`

Reference metadata:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/pack_metadata.json`

## Result

- BreadBoard (`bb_hilbert_like`): `5/6`
- Hilbert maintained fork (`hilbert_roselab`): `2/6`
- both solved: `2`
- both unsolved: `1`
- BreadBoard-only: `3`
- Hilbert-only: `0`

Per-task outcomes:
- `mathd_algebra_107` — both solved
- `mathd_algebra_156` — BreadBoard only
- `mathd_algebra_171` — both solved
- `mathd_numbertheory_530` — both unsolved
- `numbertheory_2dvd4expn` — BreadBoard only
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1` — BreadBoard only

Reference artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/bb_hilbert_like_results_v2.jsonl`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/hilbert_roselab_results_v1.jsonl`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_validation_report_v2.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_pilot_report_v2.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_core_noimo_minif2f_v1/cross_system_pilot_report_v2.md`

## Spend

Hilbert exact telemetry from proof stats:
- input tokens: `167,668`
- output tokens: `63,683`
- approximate OpenRouter spend at `openai/gpt-5.4` list pricing: `~$1.374415`

Per-task Hilbert spend drivers:
- `mathd_numbertheory_530` — `64,976` input / `24,765` output
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1` — `66,882` input / `29,432` output
- `mathd_algebra_156` — `23,051` input / `5,921` output

BreadBoard direct-formal runner summaries still report `0.0` estimated cost in this path, so provider-side spend is not available as an exact ledger here.

## Notes

- This tranche gives a second clean disagreement after the filtered Pack B medium slice, and it favors BreadBoard.
- BreadBoard retained the earlier `mathd_algebra_156` win and added a new win on `numbertheory_2dvd4expn`.
- Hilbert hit the configured LLM call caps on the hardest remaining arithmetic/number-theory cases:
  - `mathd_numbertheory_530`
  - `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- The remaining shared-unsolved tasks in this pack are both theorem-local proof-engineering targets, not control-path failures.
- Re-running the pack with the validated focused proof for `numbertheory_exk2powkeqapb2mulbpa2_aeq1` as a repair seed flips that theorem from unsolved to solved for BreadBoard.
- After the seeded rerun, the only remaining unsolved task in this pack is `mathd_numbertheory_530`.
