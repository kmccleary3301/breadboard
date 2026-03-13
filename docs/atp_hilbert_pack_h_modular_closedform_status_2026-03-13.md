## Pack H modular closed-form status — 2026-03-13

### Scope

- Pack: `pack_h_modular_closedform_minif2f_v1`
- Family: modular / closed-form arithmetic miniF2F slice
- Tasks:
  - `mathd_numbertheory_5`
  - `mathd_numbertheory_24`
  - `mathd_numbertheory_45`
  - `mathd_numbertheory_66`
  - `mathd_numbertheory_99`
  - `mathd_numbertheory_109`

### Initial result

- BreadBoard Hilbert-like: `2 / 6`
- Maintained Hilbert: `3 / 6`
- BreadBoard solved:
  - `mathd_numbertheory_45`
  - `mathd_numbertheory_66`
- BreadBoard unsolved:
  - `mathd_numbertheory_5`
  - `mathd_numbertheory_24`
  - `mathd_numbertheory_99`
  - `mathd_numbertheory_109`

### Focused repair pass

Focused BreadBoard reruns closed all four initial misses:

- `mathd_numbertheory_5`
  - forced the cube witness route
  - proved `t ≥ 4` by excluding `t ≤ 3`
  - finished by cube monotonicity with `gcongr`
- `mathd_numbertheory_24`
  - replaced `norm_num` with direct `native_decide`
- `mathd_numbertheory_99`
  - reduced the hypothesis to the residue class `n % 47`
  - finished with bounded `interval_cases`
- `mathd_numbertheory_109`
  - rewrote the finite sum pointwise using `h₀`
  - closed the resulting arithmetic goal with `native_decide`

Focused repair artifacts:

- subset dir: `tmp/pack_h_focus_modular_closedform_v1`
- refreshed BreadBoard rows merged into:
  - `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_h_modular_closedform_minif2f_v1/bb_hilbert_like_results_v2.jsonl`

### Current paired result

- BreadBoard Hilbert-like: `6 / 6`
- Maintained Hilbert: `3 / 6`
- Both solved:
  - `mathd_numbertheory_24`
  - `mathd_numbertheory_45`
  - `mathd_numbertheory_66`
- BreadBoard-only:
  - `mathd_numbertheory_5`
  - `mathd_numbertheory_99`
  - `mathd_numbertheory_109`

Current paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_h_modular_closedform_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_h_modular_closedform_minif2f_v1/cross_system_validation_report_v1.json`

### Spend

Maintained Hilbert exact telemetry is computed from:

- `artifacts/benchmarks/hilbert_runs/pack_h_modular_closedform_gpt54_roselab_v1/proofs/proof_stats`

Current exact total:

- input tokens: `54,201`
- output tokens: `9,469`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.277537`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- Pack H is already informative: BreadBoard solved the full valid slice while maintained Hilbert solved half.
- The failures were inexpensive theorem-local misses, not capability-wall failures.
- The next useful step is a new tranche beyond Pack H, not more work inside this pack.
