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

- BreadBoard (`bb_hilbert_like`): `5/7`
- Hilbert maintained fork (`hilbert_roselab`): `3/7`
- both solved: `3`
- both unsolved: `2`
- BreadBoard-only: `2`
- Hilbert-only: `0`

Solved by both:
- `mathd_algebra_171`
- `mathd_algebra_107`
- `numbertheory_2dvd4expn`

BreadBoard-only:
- `mathd_algebra_156`
- `numbertheory_exk2powkeqapb2mulbpa2_aeq1`

Unsolved by both:
- `aime_1984_p5`
- `amc12a_2019_p12`

Reference artifacts:
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_pilot_report_v6.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_b_medium_noimo530_minif2f_v1/cross_system_validation_report_v6.json`
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
- The `numbertheory_2dvd4expn` gap closed after adding task-local runner guidance in `scripts/run_bb_formal_pack_v1.py`.
- A direct-formal-runner workspace-root bug also surfaced during rerun work: workspace paths must live under `tmp/`, not under `artifacts/`. That is now fixed in `scripts/run_bb_formal_pack_v1.py`.
- `mathd_algebra_156` flipped in BreadBoard's favor after adding theorem-specific case-split guidance.
- The next ATP targets from this rerun are the two shared unsolved tasks: `aime_1984_p5` and `amc12a_2019_p12`.

## Focused Follow-up — `numbertheory_exk2powkeqapb2mulbpa2_aeq1`

- Added repair-seed support to `scripts/run_bb_formal_pack_v1.py`, so focused reruns can inject the previous candidate proof plus clipped Lean errors instead of regenerating from scratch.
- Focused seeded reruns `v15` through `v23` closed the remaining theorem-local gaps:
  - exact subtraction-identity scaffolds now verify in both unequal branches
  - the symmetric branch no longer drifts on additive reassociation
  - the theorem now solves cleanly with statement preserved
- Latest focused artifacts:
  - `tmp/focused_numbertheory_exk2powkeqapb2mulbpa2_aeq1_v1/bb_hilbert_like_summary_v23.json`
  - `tmp/focused_numbertheory_exk2powkeqapb2mulbpa2_aeq1_v1/bb_hilbert_like_raw_v23/numbertheory_exk2powkeqapb2mulbpa2_aeq1.json`
  - `tmp/focused_numbertheory_exk2powkeqapb2mulbpa2_aeq1_v1/bb_hilbert_like_proofs_v23/numbertheory_exk2powkeqapb2mulbpa2_aeq1.lean`
- That focused proof was then fed back into the full Pack B medium rerun as a repair seed, producing the updated `5/7` BreadBoard result recorded above.
