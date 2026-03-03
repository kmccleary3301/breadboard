# P3 Spend Attribution Baseline (V1)

Date: 2026-02-24

## Purpose

Create a minimal, consistent spend-attribution contract used by ATP/EvoLake execution artifacts and evidence bundles.

## Baseline Schema

Schema file:

- `docs/contracts/benchmarks/schemas/spend_attribution_record_v1.schema.json`

Required fields:

- `project`
- `workstream`
- `experiment`
- `run_id`
- `owner`
- `budget_usd`

Optional fields:

- `actual_estimated_cost_usd`
- `currency` (default `USD`)
- `generated_at`

## Artifact and Digest Paths

Per-run embedding:

- Include `spend_attribution` block inside run artifacts where available, e.g.:
  - `artifacts/longrun_phase2_live_pilot/*.json`
  - `artifacts/evidence_bundles/*/manifest.json`

Daily digest path convention:

- `artifacts/spend/attribution_digest_v1.<YYYYMMDD>.jsonl`

Rollup path convention:

- `artifacts/spend/rollups/spend_rollup_v1.<window>.json`
  - windows: `daily`, `weekly`, `monthly`

Rollup generator:

- `scripts/build_spend_attribution_rollup_v1.py`
  - scans artifact JSON payloads for `spend_attribution`
  - normalizes records against required attribution fields
  - emits grouped totals + over-budget counts

## Rollup Policy (V1)

1. Group by `(project, workstream, experiment, owner)`.
2. Aggregate:
   - run count
   - sum(`budget_usd`)
   - sum(`actual_estimated_cost_usd`) where present
3. Flag over-budget when aggregated actual exceeds aggregated budget.
4. Keep all raw per-run records (append-only JSONL) for auditability.

## Integration Points (Current + Near-Term)

### ATP lane

- `scripts/run_longrun_phase2_live_pilot.py`
  - accepts spend attribution args and emits normalized `spend_attribution` in output artifact.
- ATP evidence manifests:
  - `scripts/build_evidence_bundle_v1.py --spend KEY=VALUE ...`

### EvoLake lane

- Attach same `spend_attribution` keys to campaign artifacts and evidence manifests.
- Recommended `workstream=evolake`.

### C-Trees lane

- Use same contract for C-Trees experiment runs and benchmark manifests.
- Recommended `workstream=ctrees` to keep rollups queryable by lane.

## Enforcement Notes

- Missing spend attribution is allowed for local exploratory dry runs.
- Any run intended for KPI/evidence publication should include full spend attribution fields.
- Cost and capability claims should be linked through shared `run_id`.

## Over-Budget Exception Path

When a run is expected to exceed budget, it must use a declared exception path:

1. Annotate run metadata with:
   - `spend_exception=true`
   - `spend_exception_reason=<short reason>`
   - `spend_exception_approver=<owner>`
2. Keep original `budget_usd` unchanged (no silent budget rewriting).
3. Include exception fields in evidence packet notes.
4. Require follow-up remediation item if repeated overrun occurs in consecutive windows.
