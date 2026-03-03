# Kernel Contract Pack V1

This document defines the initial Kernel Contract Pack used to protect BreadBoard generalization and parity-critical ABI surfaces.

## Purpose

- Freeze minimal kernel-level contracts that must remain stable.
- Enforce compatibility review for changes to these contracts.
- Provide a hash-locked baseline for CI validation.

## Contract Pack Contents

1. `docs/conformance/schemas/bb.event.v1.schema.json`
2. `docs/conformance/schemas/bb.tool_call.v1.schema.json`
3. `docs/conformance/schemas/bb.tool_result.v1.schema.json`
4. `docs/conformance/schemas/bb.extension_manifest.v1.schema.json`
5. `docs/conformance/schemas/bb.session_artifact.v1.schema.json`
6. `docs/conformance/schemas/bb.replay_determinism_report.v1.schema.json`
7. `docs/conformance/schemas/bb.reliability_lane_report.v1.schema.json`
8. `docs/contracts/policies/ARCHITECTURE_CHANGE_REQUEST_TEMPLATE_V1.md`
9. `docs/contracts/policies/ROLLBACK_PROTOCOL_CHECKLIST_V1.md`
10. `docs/contracts/policies/GENERALIZATION_IMPACT_TEMPLATE_V1.md`
11. `docs/contracts/policies/kernel_danger_zone_manifest_v1.json`

## Enforcement

- Hash snapshot source of truth: `docs/contracts/policies/kernel_contract_pack_v1_manifest.json`
- Validation command:
  - `python scripts/check_kernel_contract_pack_v1.py --json`
- CI gate:
  - runs in PR checks and fails on missing/mismatched files.

## Change Policy

Any modification to the contract pack requires:

1. ACR/ADR decision artifact as required by danger-zone policy.
2. Manifest hash update in the same change.
3. Validation evidence from `check_kernel_contract_pack_v1.py`.
