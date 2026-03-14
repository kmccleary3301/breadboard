# DARWIN Phase-1 T0 Bootstrap Status

Date: 2026-03-13

## Scope completed

- DARWIN contract pack v0 added under `docs/contracts/darwin/`
- typed validation helpers added under `breadboard_ext/darwin/contracts.py`
- lane registry v0 added
- policy registry v0 added
- claim ladder / evidence-gate doc added
- weekly evidence packet template added
- schema validation script added
- bootstrap campaign-spec emitter added for:
  - `lane.atp`
  - `lane.harness`
  - `lane.systems`

## Validation

- `pytest -q tests/test_darwin_contract_pack_v0.py tests/test_bootstrap_darwin_campaign_specs_v0.py`
- `python scripts/validate_darwin_contract_pack_v0.py --json`
- `python scripts/bootstrap_darwin_campaign_specs_v0.py --json`
- `python -m py_compile breadboard_ext/darwin/contracts.py scripts/validate_darwin_contract_pack_v0.py scripts/bootstrap_darwin_campaign_specs_v0.py tests/test_darwin_contract_pack_v0.py tests/test_bootstrap_darwin_campaign_specs_v0.py`

## Produced artifacts

- contract registries:
  - `docs/contracts/darwin/registries/lane_registry_v0.json`
  - `docs/contracts/darwin/registries/policy_registry_v0.json`
- bootstrap manifest:
  - `artifacts/darwin/bootstrap/bootstrap_manifest_v0.json`

## Immediate next step

Start the first shared-lane T1 baseline layer on these contracts:

1. emit baseline weekly evidence packet from live bootstrap metadata
2. materialize baseline topology family runner scaffolding
3. stand up a DARWIN scorecard surface for ATP / harness / systems
