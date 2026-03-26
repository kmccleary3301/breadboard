## Pack I divisors/mod mix status — 2026-03-13

### Scope

- Pack: `pack_i_divisors_modmix_minif2f_v1`
- Family: divisors / gcd / modular-closed-form miniF2F slice
- Tasks:
- `mathd_numbertheory_127`
- `mathd_numbertheory_149`
- `mathd_numbertheory_169`
- `mathd_numbertheory_185`
- `mathd_numbertheory_221`
- `mathd_numbertheory_233`

### Current paired result

- BreadBoard Hilbert-like: `6 / 6`
- Maintained Hilbert: `3 / 6`
- BreadBoard-only:
  - `mathd_numbertheory_149`
  - `mathd_numbertheory_221`
  - `mathd_numbertheory_233`
- Both solved:
  - `mathd_numbertheory_127`
  - `mathd_numbertheory_169`
  - `mathd_numbertheory_185`

Per-task rows:
- `mathd_numbertheory_127` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_149` — BreadBoard `SOLVED`, Hilbert `UNSOLVED`
- `mathd_numbertheory_169` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_185` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_221` — BreadBoard `SOLVED`, Hilbert `UNSOLVED`
- `mathd_numbertheory_233` — BreadBoard `SOLVED`, Hilbert `UNSOLVED`

Current paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/cross_system_validation_report_v1.json`

BreadBoard focused repair artifacts:

- subset dir: `tmp/pack_i_focus_221_v1`
- refreshed BreadBoard rows merged into: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/bb_hilbert_like_results_v3.jsonl`
- refreshed Hilbert rows merged into: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_i_divisors_modmix_minif2f_v1/hilbert_roselab_results_v2.jsonl`

### Spend

Maintained Hilbert exact telemetry is computed from:

- `artifacts/benchmarks/hilbert_runs/pack_i_divisors_modmix_gpt54_roselab_v2/proofs/proof_stats`
- with `mathd_numbertheory_221` replaced by the corrected focused rerun at `artifacts/benchmarks/hilbert_runs/pack_i_focus_221_gpt54_roselab_v1/proofs/proof_stats/mathd_numbertheory_221_stats.json`

Current exact total:

- input tokens: `59,750`
- output tokens: `14,194`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.362285`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- Pack I is now clean on canonicalized statements.
- BreadBoard saturated the slice after a single focused repair on `mathd_numbertheory_221`.
- Maintained Hilbert remained unsolved on the corrected focused `221` rerun, so the Pack I comparison is no longer dependent on the earlier lowercase `finset` extraction bug.
- The next useful step is a new tranche beyond Pack I, not more work inside this pack.
