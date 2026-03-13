# ATP Hilbert Scoreboard Status — 2026-03-13

This note records the first program-level scoreboard artifact built from the locked canonical ATP/Hilbert baselines.

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.md`

Input baseline lock:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`

Interpretation:

- Headline totals count only `canonical_primary`, `canonical_rollup`, and `boundary_stress` entries.
- Supporting filtered/split/focus lanes remain visible in the scoreboard but are not counted in the headline totals, which prevents double-counting Pack D and Pack E follow-up work.
- Hilbert spend in the scoreboard is exact only where the status notes provide an exact maintained-Hilbert figure.
- BreadBoard spend remains absent from the program-level scoreboard until the direct formal runner emits a usable provider-side ledger.
