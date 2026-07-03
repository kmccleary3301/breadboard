# Runtime Pool Runbook

M8 validates local runtime signatures, exact pool routing, worker quarantine, local Ray toy execution, telemetry, and warm-vs-cold reporting.

Main command:

```bash
python scripts/rl_phase1/run_ray_warm_pool_probe.py
```

Artifacts:

```text
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe/ray_probe_report.json
docs_tmp/ZYPHRA/RL_PHASE_1/runs/m8_ray_warm_pool_probe/warm_vs_cold_report.json
```

Claim boundary: local Ray local-mode prototype only. Not production distributed rollout, not MI300X validation, not trainer scale.
