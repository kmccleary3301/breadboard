# ATP Hilbert Rollup Status — 2026-03-13

Rollup automation is now available via:

- `scripts/build_atp_hilbert_rollup_v1.py`

The rollup entrypoint rebuilds:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/canonical_baseline_index_v1.md`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.md`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/rollup_manifest_v1.json`

This removes the need to regenerate the canonical index and scoreboard manually or in the correct order.
