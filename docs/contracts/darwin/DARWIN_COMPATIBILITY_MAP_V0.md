# DARWIN / EvoLake Compatibility Map V0

Date: 2026-03-14

## Purpose

Freeze the near-term coexistence rules between the canonical DARWIN program identity and the legacy EvoLake runtime surface already present in the repository.

## Canonical naming

- Program name: `DARWIN`
- Legacy alias: `EvoLake`

DARWIN is the canonical planning, control-plane, and evaluation-plane identity. EvoLake remains as a compatibility/runtime alias until a dedicated migration tranche lands.

## Stable legacy surfaces retained for now

These remain valid and must **not** be renamed during Phase-1:

- extension package path:
  - `breadboard_ext/evolake/`
- extension id:
  - `evolake`
- legacy route prefix:
  - `/ext/evolake`
- legacy schema ids already emitted by existing bridge/runtime code:
  - `breadboard.evolake.campaign_payload.v1`
  - `breadboard.evolake.campaign_record.v1`
  - `breadboard.evolake.campaign_checkpoint.v1`
  - `breadboard.evolake.replay_manifest.v1`
- existing nightly producer naming:
  - `scripts/evolake_toy_campaign_nightly.py`
  - `.github/workflows/evolake_toy_campaign_nightly.yml`

## New DARWIN-native surfaces

These are canonical for Phase-1 platform buildout:

- `docs/contracts/darwin/`
- `breadboard_ext/darwin/`
- `artifacts/darwin/`
- DARWIN control-plane docs, registries, scorecards, weekly packets, evidence bundles, and claim records

## Migration posture

- No silent rename of legacy `evolake` paths in Phase-1.
- New platform contracts and governance artifacts are DARWIN-native.
- Any future rename of runtime identifiers requires a dedicated migration tranche with:
  - compatibility matrix,
  - artifact rewrite plan,
  - rollback plan,
  - replay validation.
