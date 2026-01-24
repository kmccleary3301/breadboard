# C-Trees Behavioral Benchmark Recipe

This doc captures the minimal, repeatable steps for running and freezing the E2E
benchmark used to validate C-Trees behavior (online, Ray).

## 1) Freeze the benchmark set

Generate a manifest with scenario + config hashes:

```bash
python scripts/ctlab_freeze_benchmark.py \
  --scenarios experiments/ctrees/e2e_scenarios/e2e_no_tool_recall_01.json \
             experiments/ctrees/e2e_scenarios/e2e_single_tool_lookup_01.json \
  --config implementations/profiles/gpt5_nano_enhanced.yaml \
  --out experiments/ctrees/benchmarks/ctrees_e2e_frozen_manifest.json \
  --label ctrees_e2e_frozen_v1
```

The manifest pins:
- scenario ids + sha256
- config path + sha256 (including model/provider)

Only update the manifest with an explicit version bump.

## 2) Run E2E scenarios (online)

```bash
python scripts/ctlab_e2e_runner.py \
  experiments/ctrees/e2e_scenarios/e2e_no_tool_recall_01.json \
  --config implementations/profiles/gpt5_nano_enhanced.yaml
```

Optional flags:
- `--conditions <id...>`: run only the selected condition ids.
- `--repeat N`: repeat each condition N times (defaults to scenario.repetitions.dev).
- `--ablation`: add ablation variants (toggle C-Trees on/off) per condition.
- `--allow-missing-metrics`: relax QC for missing metrics/turns.jsonl (not recommended).

Each run writes a bundle (`ctrees_lab_bundle/`) that includes:
- `meta/run_summary.json`
- `metrics/turns.jsonl`
- `metrics/tools.jsonl` (best effort)

## 3) QC expectations

E2E runs should pass `ctlab.py qc` with metrics required:

```bash
BREADBOARD_QC_REQUIRE_METRICS=1 python scripts/ctlab.py qc <bundle_dir>
```

Missing metrics or incomplete runs should be treated as failures for benchmark runs.

## 4) Reporting

Offline results are summarized via:

```bash
python scripts/ctlab_experiments_v2.py --tiers offline standard --summary --report experiments/ctrees/reports/v2_report.md
```

For E2E runs, aggregate `*_results.json` files in `logs/ctrees_e2e_runs/` and
track:
- `score.ok`
- `qc_ok`
- `usd_estimate`
- `latency_avg_seconds`

## Paper-ready results fields (minimum)

- Baseline vs C-Trees success rates (overall + per tier).
- Paired delta mean with 95% bootstrap CI.
- Wins/losses/ties and sign-test p-value.
- Model + policy version pins (from frozen manifest).
- Cost + latency summaries when available.

## Known limitations

- Scenario `prompt.system` is not currently injected (system prompt is defined by config).
- Scenario tool allow/deny lists are not yet enforced; add policy overrides if needed.
