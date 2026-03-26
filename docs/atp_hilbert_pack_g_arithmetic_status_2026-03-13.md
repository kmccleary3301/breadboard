## Pack G arithmetic sanity status — 2026-03-13

### Scope

- Pack: `pack_g_arithmetic_sanity_minif2f_v1`
- Family: closed-form / bounded arithmetic miniF2F slice
- Tasks:
  - `mathd_numbertheory_3`
  - `mathd_numbertheory_12`
  - `mathd_numbertheory_237`
  - `mathd_numbertheory_299`
  - `mathd_numbertheory_353`
  - `mathd_numbertheory_430`

### Initial result

- BreadBoard Hilbert-like: `4 / 6`
- Maintained Hilbert: `4 / 6`
- Both solved:
  - `mathd_numbertheory_3`
  - `mathd_numbertheory_12`
  - `mathd_numbertheory_237`
  - `mathd_numbertheory_299`
- Shared unsolved:
  - `mathd_numbertheory_353`
  - `mathd_numbertheory_430`

Initial paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_validation_report_v1.json`

### Focused repair pass

Focused BreadBoard reruns closed both shared misses:

- `mathd_numbertheory_353`
  - avoided `subst h₀`
  - used the direct closed-goal route:
    - `rw [h₀]`
    - `native_decide`
- `mathd_numbertheory_430`
  - replaced triple `interval_cases` with the compressed arithmetic route:
    - `have hb_eq : b = 3 * a := by omega`
    - `have hc_eq : c = 4 * a := by omega`
    - `rw [hb_eq, hc_eq] at h₈`
    - `nlinarith [h₈]`

Focused repair artifacts:

- subset dir: `tmp/pack_g_focus_353_430_v1`
- refreshed BreadBoard rows merged into:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/bb_hilbert_like_results_v2.jsonl`

### Current paired result

- BreadBoard Hilbert-like: `6 / 6`
- Maintained Hilbert: `4 / 6`
- Both solved:
  - `mathd_numbertheory_3`
  - `mathd_numbertheory_12`
  - `mathd_numbertheory_237`
  - `mathd_numbertheory_299`
- BreadBoard-only:
  - `mathd_numbertheory_353`
  - `mathd_numbertheory_430`

Current paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_pilot_report_v2.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_g_arithmetic_sanity_minif2f_v1/cross_system_validation_report_v2.json`

### Spend

Maintained Hilbert exact telemetry from proof stats:

- input tokens: `55,321`
- output tokens: `17,082`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.394533`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- Pack G is saturated under the current bounded settings.
- The two focused fixes were theorem-local proof-engineering improvements, not harness-control changes.
- BreadBoard cleanly outperformed the maintained Hilbert fork on this valid arithmetic sanity slice.
