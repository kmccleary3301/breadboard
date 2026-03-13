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

### Result

- BreadBoard Hilbert-like: `3 / 4`
- Maintained Hilbert: `1 / 4`
- BreadBoard-only wins: `mathd_algebra_73`, `mathd_algebra_131`
- Both solved: `mathd_algebra_48`
- Both unsolved: `mathd_algebra_77`

Paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_pilot_report_v2.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_focus_minif2f_v1/cross_system_validation_report_v2.json`

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
- `mathd_algebra_77` remains the only unresolved task in this focused algebra tranche.

### Recommendation

- Treat the Pack E focus tranche as complete evidence of a BreadBoard advantage on the algebra follow-up lane.
- Use `mathd_algebra_77` as the next algebra-specific repair target, not the entire Pack E family.
