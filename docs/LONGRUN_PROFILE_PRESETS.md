# LongRun Profile Presets

BreadBoard now ships three preset longrun profiles for policy experiments:

- `agent_configs/longrun_conservative_v1.yaml`
- `agent_configs/longrun_balanced_v1.yaml`
- `agent_configs/longrun_aggressive_v1.yaml`

## Safety Notes
1. These presets are **opt-in** and separate from parity/E4 configs.
2. Parity/E4 configs must keep `long_running.enabled=false`.
3. Use `BREADBOARD_LONGRUN_ENABLE=0` to force-disable longrun globally.

## Preset Intent

### Conservative
- Lower episode/step budgets.
- Smoke-only verification.
- Strong boundedness and low spend target.

### Balanced
- Mid-range budgets.
- Smoke + full verification tiers.
- Good default for internal policy iteration.

### Aggressive
- Higher budgets and retries.
- Feature-file queue backend.
- Best for hard tasks when spending more for exploration is acceptable.

## Example Usage
```bash
python main.py agent_configs/longrun_balanced_v1.yaml -t "Implement feature X with tests"
```

```bash
AGENT_SCHEMA_V2_ENABLED=1 BREADBOARD_LONGRUN_ENABLE=1 \
python main.py agent_configs/longrun_conservative_v1.yaml -i
```

## Related Gates
- Strict parity lane: `.github/workflows/longrun-phase1-gate.yml`
- Warn/strict pilot lane: `.github/workflows/longrun-phase2-warn.yml`
