## Pack J residue/gcd mix status — 2026-03-13

### Scope

- Pack: `pack_j_residue_gcd_mix_minif2f_v1`
- Family: residue / gcd-lcm / closed modular arithmetic miniF2F slice
- Tasks:
- `mathd_numbertheory_34`
- `mathd_numbertheory_100`
- `mathd_numbertheory_212`
- `mathd_numbertheory_239`
- `mathd_numbertheory_254`
- `mathd_numbertheory_320`

### Current paired result

- BreadBoard Hilbert-like: `6 / 6`
- Maintained Hilbert: `4 / 6`
- BreadBoard-only:
  - `mathd_numbertheory_239`
  - `mathd_numbertheory_320`
- Both solved:
  - `mathd_numbertheory_34`
  - `mathd_numbertheory_100`
  - `mathd_numbertheory_212`
  - `mathd_numbertheory_254`

Per-task rows:
- `mathd_numbertheory_34` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_100` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_212` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_239` — BreadBoard `SOLVED`, Hilbert `UNSOLVED`
- `mathd_numbertheory_254` — BreadBoard `SOLVED`, Hilbert `SOLVED`
- `mathd_numbertheory_320` — BreadBoard `SOLVED`, Hilbert `UNSOLVED`

Current paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_j_residue_gcd_mix_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_j_residue_gcd_mix_minif2f_v1/cross_system_validation_report_v1.json`

### Spend

Maintained Hilbert exact telemetry is computed from:

- `artifacts/benchmarks/hilbert_runs/pack_j_residue_gcd_mix_gpt54_roselab_v1/proofs/proof_stats`

Current exact total:

- input tokens: `22,685`
- output tokens: `5,292`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.136093`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- Pack J is a clean bounded tranche with no invalid extracted statements.
- BreadBoard saturated the slice without focused repairs.
- Maintained Hilbert missed the two bounded-residue tasks `mathd_numbertheory_239` and `mathd_numbertheory_320`.
- The next useful step is a new tranche beyond Pack J, not more work inside this pack.
