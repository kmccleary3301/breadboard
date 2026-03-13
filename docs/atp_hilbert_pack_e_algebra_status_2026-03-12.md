## Pack E algebra core status — 2026-03-12

### Scope

- Pack: `pack_e_algebra_core_minif2f_v1`
- Family: algebra-heavy miniF2F slice
- Tasks:
  - `mathd_algebra_48`
  - `mathd_algebra_101`
  - `mathd_algebra_410`
  - `mathd_algebra_73`
  - `mathd_algebra_77`
  - `mathd_algebra_131`

### Result

- BreadBoard Hilbert-like: `2 / 6`
- Maintained Hilbert: `2 / 6`
- Discordant pairs: `0`

Paired artifacts:

- report: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_pilot_report_v1.json`
- validation: `artifacts/benchmarks/hilbert_comparison_packs_v2/pack_e_algebra_core_minif2f_v1/cross_system_validation_report_v1.json`

### Per-task outcome

- `mathd_algebra_101` — both solved
- `mathd_algebra_410` — both solved
- `mathd_algebra_48` — both unsolved
- `mathd_algebra_73` — both unsolved
- `mathd_algebra_77` — both unsolved
- `mathd_algebra_131` — both unsolved

### Spend

Maintained Hilbert exact telemetry from proof stats:

- input tokens: `90,466`
- output tokens: `21,852`
- estimated cost at OpenRouter `openai/gpt-5.4` list pricing: `~$0.553945`

BreadBoard direct-formal runner still does not emit a usable provider-side spend ledger in this path.

### Read

- Pack E is a clean algebra family cross-check, but it does not differentiate the systems under current bounded settings.
- Both systems are strong on the two easiest tasks and fail on the same four harder algebra tasks.
- The highest-yield follow-up is theorem-local algebra guidance for:
  - `mathd_algebra_48` (`Complex.I` / statement-normalization issues)
  - `mathd_algebra_131` (near-miss root/sum-product route)
  - `mathd_algebra_73`
  - `mathd_algebra_77`

### Recommendation

- Treat Pack E as completed baseline evidence, not an optimization target yet.
- If we want more algebra signal, create a focused follow-up tranche for the four shared-unsolved tasks instead of broadening immediately.
