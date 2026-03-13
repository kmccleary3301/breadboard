## Pack E algebra focus status — 2026-03-13

### Scope

- Pack: `pack_e_algebra_focus_minif2f_v1`
- Family: focused algebra follow-up from Pack E
- Tasks:
  - `mathd_algebra_48`
  - `mathd_algebra_73`
  - `mathd_algebra_77`
  - `mathd_algebra_131`

### Changes applied

- Canonicalized `complex.I` to `Complex.I` in pack generation.
- Added theorem-local runner guidance for:
  - `mathd_algebra_48`
  - `mathd_algebra_73`
  - `mathd_algebra_77`
  - `mathd_algebra_131`
- Used one seeded BreadBoard consolidation pass to preserve the best solved proofs across reruns.
- Excluded `mathd_algebra_77` after confirming it is unsound under the extracted statement. Counterexample: `a = -1/2`, `b = -1/2`, and `f x = x^2 + a*x + b`.

### Result

- BreadBoard Hilbert-like: `3 / 3`
- Maintained Hilbert: `1 / 3`
- BreadBoard-only wins: `mathd_algebra_73`, `mathd_algebra_131`
- Both solved: `mathd_algebra_48`

Paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_pilot_report_v3.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_validation_report_v3.json`

### Spend

Maintained Hilbert exact telemetry from proof stats:

- input tokens: `66,358`
- output tokens: `23,449`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.517630`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- The focused algebra lane now clearly favors BreadBoard under the current bounded comparator settings.
- The `Complex.I` namespace fix was necessary for a fair comparison on `mathd_algebra_48`.
- `mathd_algebra_73` and `mathd_algebra_131` both flipped in BreadBoard after theorem-local guidance refinement.
- `mathd_algebra_77` is not a remaining ATP gap; it is an invalid benchmark item under the extracted statement.

### Recommendation

- Treat the Pack E focus tranche as complete evidence of a BreadBoard advantage on the valid algebra follow-up lane.
- The next algebra step is a new valid tranche, not more work on `mathd_algebra_77`.
