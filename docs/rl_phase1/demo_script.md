# Demo Script

1. Show `docs_tmp/ZYPHRA/RL_PHASE_1/BB_ZYPHRA_RL_PHASE_1_SCORECARD.yaml`.
2. Load `examples/rl_env_packages/swe_toy_patch/env_package.yaml` and point out provenance, hardening, renderer, replay, and export eligibility.
3. Run:

```bash
python scripts/rl_phase1/run_swe_probe.py
```

4. Inspect:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/qc_report.json
```

5. Run:

```bash
python scripts/rl_phase1/export_verl_probe.py
```

6. Inspect:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/smoke_consumer_report.json
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.jsonl
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe/verl_probe_rows.parquet
```

7. Run:

```bash
python scripts/rl_phase1/run_ray_warm_pool_probe.py
python scripts/rl_phase1/build_adapter_probe_reports.py
```

8. Close with the claim boundary: local controlled SWE toy, JSONL/Parquet probe export, local Ray prototype, fixture/jsonl adapter probes; no production or external benchmark support yet.
