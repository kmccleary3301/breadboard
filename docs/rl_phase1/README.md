# BreadBoard RL Phase 1

BreadBoard RL Phase 1 is a local, probe-backed rollout substrate for Zyphra RL use cases. It preserves BreadBoard graph/replay/runtime truth and emits trainer-shaped projections without making any trainer or external framework canonical.

Current verified boundary: M0-M11 are validated locally. M12 8xMI300X validation remains blocked until target hardware execution.

## What Exists

| Area | Path |
| --- | --- |
| EnvPackage IR | `breadboard/rl/env_package/` |
| Renderer/token records | `breadboard/rl/renderer/`, `breadboard/rl/export/token_record.py` |
| Session/runtime lifecycle | `breadboard/rl/session/`, `breadboard/rl/runtime/` |
| State/trace/replay | `breadboard/rl/state/`, `breadboard/rl/trace/`, `breadboard/rl/replay/` |
| Hardening/probes | `breadboard/rl/security/` |
| Controlled SWE run | `breadboard/rl/e2e/swe_probe.py` |
| VeRL-shaped JSONL/Parquet probe | `breadboard/rl/export/verl.py` |
| Adapter probe reports | `breadboard/rl/adapters/` |

## Main Commands

```bash
python scripts/rl_phase1/run_swe_probe.py
python scripts/rl_phase1/export_verl_probe.py
python scripts/rl_phase1/run_ray_warm_pool_probe.py
python scripts/rl_phase1/build_adapter_probe_reports.py
python -m pytest tests/rl -q
```

## Claim Boundary

Allowed: local controlled SWE toy slice, VeRL-shaped JSONL/Parquet probe, local Ray worker prototype, and fixture/jsonl adapter probe reports.

Forbidden: production RL rollouts, external benchmark support, production VeRL/BenchFlow/ORS/Prime integrations, trainer execution, and MI300X scale validation.
