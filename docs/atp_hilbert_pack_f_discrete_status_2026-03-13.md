## Pack F discrete arithmetic mix status — 2026-03-13

### Scope

- Pack: `pack_f_discrete_arithmetic_mix_minif2f_v1`
- Family: bounded discrete arithmetic / contest-style miniF2F slice
- Tasks:
  - `amc12a_2015_p10`
  - `aime_1991_p1`
  - `amc12a_2008_p4`
  - `amc12_2001_p9`
  - `mathd_numbertheory_48`
  - `mathd_numbertheory_33`

### Initial paired result

- BreadBoard Hilbert-like: `1 / 6`
- Maintained Hilbert: `3 / 6`
- Baseline-only wins:
  - `amc12_2001_p9`
  - `mathd_numbertheory_33`
- Both solved:
  - `mathd_numbertheory_48`

Initial paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_validation_report_v1.json`

### Focused repair pass

Two theorem-local repairs were enough to close the initial Hilbert-only gaps on the BreadBoard side:

- `amc12_2001_p9`
  - fixed the product-to-`600` rewrite route by inserting an explicit
    `have hm : (500 : ℝ) * ((6 : ℝ) / 5) = 600 := by norm_num`
    before `simpa`.
- `mathd_numbertheory_33`
  - replaced the broken `Nat.modEq_iff_dvd'` route with direct bounded
    `interval_cases n <;> norm_num at h₁ ⊢`.

Focused repair artifacts:

- focused subset dir: `tmp/pack_f_focus_hilbertwins_v1`
- refreshed BreadBoard rows merged into:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/bb_hilbert_like_results_v3.jsonl`

### Current paired result

- BreadBoard Hilbert-like: `3 / 6`
- Maintained Hilbert: `3 / 6`
- Both solved:
  - `mathd_numbertheory_48`
  - `amc12_2001_p9`
  - `mathd_numbertheory_33`
- Shared unsolved:
  - `amc12a_2015_p10`
  - `aime_1991_p1`
  - `amc12a_2008_p4`

Current paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_pilot_report_v2.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_f_discrete_arithmetic_mix_minif2f_v1/cross_system_validation_report_v2.json`

### Spend

Maintained Hilbert exact telemetry from proof stats:

- input tokens: `68,466`
- output tokens: `29,348`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.611385`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Notes

- A full-pack BreadBoard rerun with repair seeds regressed solved tasks and is intentionally not treated as canonical evidence.
- The canonical BreadBoard result for Pack F is the merged `v3` rowset:
  - base full-pack `v1` rows
  - focused repairs for `amc12_2001_p9` and `mathd_numbertheory_33`
- The next useful work is theorem-local focus on the remaining shared-unsolved trio:
  - `amc12a_2015_p10`
  - `aime_1991_p1`
  - `amc12a_2008_p4`
