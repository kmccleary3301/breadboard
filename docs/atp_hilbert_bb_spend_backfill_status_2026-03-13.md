# ATP Hilbert BreadBoard Spend Backfill Status — 2026-03-13

BreadBoard spend backfill is now reconstructed from canonical ATP artifacts without rerunning the packs.

Artifacts:

- `artifacts/benchmarks/hilbert_comparison_packs_v2/bb_spend_backfill_v1.json`
- `artifacts/benchmarks/hilbert_comparison_packs_v2/scoreboard_v1.json`

Method:

- find canonical pack entries from `canonical_baseline_index_v1.json`
- collect candidate BreadBoard raw diagnostics from the canonical pack dir, referenced pack dirs, and referenced focused `tmp/` dirs
- choose the latest diagnostic per task
- recover `run_dir` from the raw diagnostic
- recover prompt/completion tokens and estimated OpenRouter cost from `meta/run_summary.json`

Current limitation:

- BreadBoard spend is still estimated from logged token usage plus static OpenRouter list pricing
- it is not yet a provider-native billing ledger
