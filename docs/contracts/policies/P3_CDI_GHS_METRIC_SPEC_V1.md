# P3 CDI/GHS Metric Spec (V1)

Date: 2026-02-24

## Purpose

Define the first operational spec for:

- `CDI`: Coupling Drift Index (kernel contamination pressure),
- `GHS`: Generalization Health Score (overall generalization condition).

This spec is for KPI pipeline integration and warning/error thresholding. It is intentionally conservative and uses currently-available artifact signals.

## Inputs and Artifact Mapping

Primary KPI generator: `scripts/generate_p2_kpi_snapshot_v1.py`

Signal inputs and source artifacts:

1. Request-body gate:
   - `artifacts/request_body_hash_gate_report.local.json`
   - fallback: `artifacts/request_body_hash_gate_report.json`
2. Tool-schema gate:
   - `artifacts/tool_schema_hash_gate_report.local.json`
   - fallback: `artifacts/tool_schema_hash_gate_report.json`
3. Event-envelope gate:
   - `artifacts/event_envelope_snapshot_gate_report.local.json`
   - fallback: `artifacts/event_envelope_snapshot_gate_report.json`
4. Kernel-extension boundary:
   - `artifacts/kernel_ext_boundary_report.local.json`
   - fallback: `artifacts/kernel_ext_boundary_report.json`
5. Kernel contract-pack hash guard:
   - `artifacts/kernel_contract_pack_report.local.json`
   - fallback: `artifacts/kernel_contract_pack_report.json`
6. Danger-zone touch count (optional):
   - `artifacts/kernel_danger_zone_touch_report.local.json`
   - fallback: `artifacts/kernel_danger_zone_touch_report.json`

The KPI output stores resolved source paths under:
- `generalization.metric_sources`

## Metric Fields

KPI fields emitted under `generalization`:

- `core_imports_ext_count`
- `kernel_danger_zone_files_touched`
- `stable_request_body_diff_count`
- `stable_tool_schema_hash_diff_count`
- `stable_event_envelope_diff_count`
- `kernel_contract_pack_drift_count`
- `extension_disable_mode_pass_rate`
- `coupling_drift_index`
- `coupling_drift_band`
- `generalization_health_score`
- `generalization_health_band`
- `critical_gate_failures`

## Formulas (V1)

### Component counts

- `stable_request_body_diff_count = len(missing_files) + len(extra_files) + len(changed_files)` from request-body gate report.
- `stable_tool_schema_hash_diff_count = len(missing_files) + len(extra_files) + len(changed_files)` from tool-schema gate report.
- `stable_event_envelope_diff_count = len(missing_files) + len(changed_files)` (event report has no `extra_files`).
- `core_imports_ext_count = kernel_ext_boundary_report.violations_count`.
- `kernel_contract_pack_drift_count = missing_count + mismatched_count + duplicate_count`.
- `extension_disable_mode_pass_rate = 1.0 if all critical generalization gates are true else 0.0` (placeholder until dedicated disable-mode suite artifact is wired).

### CDI

`CDI = clamp_0_100(`

- `40 * core_imports_ext_count`
- `+ 2 * kernel_danger_zone_files_touched`
- `+ 15 * stable_request_body_diff_count`
- `+ 15 * stable_tool_schema_hash_diff_count`
- `+ 10 * stable_event_envelope_diff_count`
- `+ 10 * kernel_contract_pack_drift_count`
- `+ 8` if `extension_disable_mode_pass_rate < 1.0`

`)`

### GHS

- `critical_gate_failures = count_false([request_body_gate_ok, tool_schema_gate_ok, event_envelope_gate_ok, kernel_ext_boundary_ok, kernel_contract_pack_ok])`
- `GHS = clamp_0_100(100 - CDI - 5 * critical_gate_failures)`

## Threshold Policy (Initial)

### CDI bands

- `healthy`: `CDI <= 10`
- `warn`: `10 < CDI <= 25`
- `error`: `CDI > 25`

### GHS bands

- `healthy`: `GHS >= 90`
- `warn`: `75 <= GHS < 90`
- `error`: `GHS < 75`

### Alarm policy (proposed)

1. Emit warning if `coupling_drift_band == "warn"` for one run.
2. Emit error if `coupling_drift_band == "error"` in any run.
3. Emit error if `generalization_health_band == "error"` in any run.
4. Emit warning if `generalization_health_band == "warn"` for 3 consecutive nightly runs.

## Integration Notes

- This V1 spec keeps formulas explicit and deterministic.
- When extension-disable suite artifact lands, replace placeholder `extension_disable_mode_pass_rate` logic with measured pass-rate input.
- If additional kernel drift signals are added, version this document (`V2`) and update KPI schema + alarms in same change.
