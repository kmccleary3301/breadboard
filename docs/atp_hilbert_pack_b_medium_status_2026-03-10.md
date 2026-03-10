# ATP Hilbert Pack B Medium Status — 2026-03-10

This note records the bounded Pack B comparison used after excluding the spend-heavy `imo_1977_p6` case and the sticky `mathd_numbertheory_530` case.

Important follow-up:
- `mathd_numbertheory_780` is now treated as invalid for active comparison packs.
- Counterexample under the extracted `ℕ` theorem semantics: `m = 11`, `x = 2`.
- Reason: `Nat` subtraction truncates, so `(2 - 36) % 11 = 0`, which makes the hypotheses true while the conclusion `m = 43` is false.
- The historical `2/8` Pack B medium result is stale. The corrected rerun below uses the filtered 7-task slice.

## Pack

Path:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/`

Tasks:
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- `mathd_algebra_156`
- `mathd_algebra_171`
- `aime_1984_p5`
- `amc12a_2019_p12`
- `numbertheory_2dvd4expn`
- `mathd_algebra_107`

## Corrected Result

- BreadBoard (`bb_hilbert_like`): `2/7`
- Hilbert maintained fork (`hilbert_roselab`): `3/7`
- both solved: `2`
- both unsolved: `4`
- discordant pairs: `1`

Solved by both:
- `mathd_algebra_171`
- `mathd_algebra_107`

Unsolved by both:
- `mathd_algebra_156`
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- `aime_1984_p5`
- `amc12a_2019_p12`

Hilbert-only solve:
- `numbertheory_2dvd4expn`

Reference artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_pilot_report_v3.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_validation_report_v3.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/pack_metadata.json`

## Spend

Hilbert exact telemetry from proof stats:
- input tokens: `114,690`
- output tokens: `38,234`
- approximate OpenRouter spend at `openai/gpt-5.4` list pricing: `~$0.860235`

BreadBoard exact provider-side spend is not available from the current direct formal runner artifacts.

## Notes

- `mathd_numbertheory_530` required theorem-header canonicalization in `scripts/build_hilbert_comparison_packs_v2.py`.
- `mathd_numbertheory_780` is excluded via the invalid-task filter in `scripts/build_hilbert_comparison_packs_v2.py`.
- The next targeted ATP task from this rerun is `numbertheory_2dvd4expn`, the only corrected Pack B medium discordant task.
