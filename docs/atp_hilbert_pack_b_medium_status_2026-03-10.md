# ATP Hilbert Pack B Medium Status — 2026-03-10

This note records the bounded Pack B comparison used after excluding the spend-heavy `imo_1977_p6` case and the sticky `mathd_numbertheory_530` case.

Important follow-up:
- `mathd_numbertheory_780` is now treated as invalid for active comparison packs.
- Counterexample under the extracted `ℕ` theorem semantics: `m = 11`, `x = 2`.
- Reason: `Nat` subtraction truncates, so `(2 - 36) % 11 = 0`, which makes the hypotheses true while the conclusion `m = 43` is false.
- This means the historical `2/8` Pack B medium result should be treated as a stale baseline from before the invalid-task filter. The pack must be rerun as a 7-task slice.

## Pack

Path:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/`

Tasks:
- `mathd_numbertheory_780` (now excluded as invalid; listed here only for historical traceability)
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- `mathd_algebra_156`
- `mathd_algebra_171`
- `aime_1984_p5`
- `amc12a_2019_p12`
- `numbertheory_2dvd4expn`
- `mathd_algebra_107`

## Result

- BreadBoard (`bb_hilbert_like`): `2/8`
- Hilbert maintained fork (`hilbert_roselab`): `2/8`
- both solved: `2`
- both unsolved: `6`
- discordant pairs: `0`

Solved by both:
- `mathd_algebra_171`
- `mathd_algebra_107`

Unsolved by both:
- `mathd_algebra_156`
- `mathd_numbertheory_780` (invalid task; should not be used in future comparisons)
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`
- `aime_1984_p5`
- `amc12a_2019_p12`
- `numbertheory_2dvd4expn`

## Spend

Hilbert exact telemetry from proof stats:
- input tokens: `129,513`
- output tokens: `39,240`
- approximate OpenRouter spend at `openai/gpt-5.4` list pricing: `~$0.912382`

BreadBoard exact provider-side spend is not available from the current direct formal runner artifacts.

## Notes

- `mathd_numbertheory_530` required theorem-header canonicalization in `scripts/build_hilbert_comparison_packs_v2.py`.
- The canonical execution root that produced the artifacts was not a git worktree; this branch ports the runner/config/code changes into a real repository.
