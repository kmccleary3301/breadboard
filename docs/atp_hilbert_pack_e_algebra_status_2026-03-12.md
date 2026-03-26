## Pack E algebra core status — 2026-03-12

### Scope

- Pack: `pack_e_algebra_core_minif2f_v1`
- Family: algebra-heavy miniF2F slice
- Tasks:
  - `mathd_algebra_48`
  - `mathd_algebra_101`
  - `mathd_algebra_410`
  - `mathd_algebra_73`
  - `mathd_algebra_131`
- Excluded invalid task:
  - `mathd_algebra_77`

### Result

- BreadBoard Hilbert-like: `5 / 5`
- Maintained Hilbert: `3 / 5`
- BreadBoard-only wins: `mathd_algebra_73`, `mathd_algebra_131`

Paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_pilot_report_v2.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_validation_report_v2.json`

### Per-task outcome

- `mathd_algebra_48` — both solved
- `mathd_algebra_101` — both solved
- `mathd_algebra_410` — both solved
- `mathd_algebra_73` — BreadBoard only
- `mathd_algebra_131` — BreadBoard only

### Spend

Maintained Hilbert exact telemetry from the merged valid-task proof stats:

- input tokens: `29,790`
- output tokens: `11,005`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.239550`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- On the valid five-task slice, Pack E now clearly favors BreadBoard.
- The `Complex.I` normalization fix was necessary to make `mathd_algebra_48` a fair comparator.
- `mathd_algebra_73` and `mathd_algebra_131` are now BreadBoard-only wins.
- `mathd_algebra_77` is not a remaining ATP gap; it is invalid under the extracted statement and is excluded from active packs.

### Recommendation

- Treat Pack E core as completed evidence of a BreadBoard advantage on the valid algebra family slice.
- The next algebra step is a new valid tranche beyond Pack E, not more work on `mathd_algebra_77`.
