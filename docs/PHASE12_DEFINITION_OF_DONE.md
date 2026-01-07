# Phase 12 — Definition of Done (Packaging)

This document defines what “Phase 12 is done” means for BreadBoard packaging.

## In scope

- The **TUI/CLI package** (`tui_skeleton/`) builds and runs as `breadboard`.
- The **engine** runs behind the CLI bridge HTTP/SSE contract.
- A user can run **doctor / ui / run** locally with reasonable defaults.
- We can publish and validate **standalone engine bundles** via GitHub Releases.
- CI provides guardrails against contract drift and packaging regressions.

## Done criteria

Phase 12 is considered **done** when all items below are true:

### CI + smokes

- `.github/workflows/ci.yml` is green on:
  - `ubuntu-latest`, `macos-latest`, `windows-latest` (TUI)
  - `ubuntu-latest` (Python targeted tests)
- CI includes:
  - TUI typecheck + unit tests + build
  - `npm pack` install smoke (cross-platform)
  - A Linux **live engine smoke** that runs `doctor` + `run` end-to-end (source engine)
  - A contract drift check that re-exports `docs/contracts/cli_bridge/*` and asserts no diff

### Contracts

- `scripts/export_cli_bridge_contracts.py` exports:
  - `docs/contracts/cli_bridge/openapi.json`
  - JSON Schemas for the event envelope + key event payloads + command request bodies
- Exported contracts are tracked in git and remain deterministic.

### Engine bundling + publishing

- `.github/workflows/release_engine_bundles.yml` can be run via `workflow_dispatch` and publishes:
  - per-platform bundle archives
  - merged `manifest.json`
- The CLI can auto-download from the release manifest:
  - `BREADBOARD_ENGINE_AUTO_DOWNLOAD=1`
  - `BREADBOARD_ENGINE_MANIFEST_URL=<release-manifest-url>`
  - `BREADBOARD_ENGINE_VERSION=<manifest.version>`
- The release runbook is accurate:
  - `docs/ENGINE_BUNDLE_RELEASE_RUNBOOK.md`

### Sandbox baseline safety

- The default “light” sandbox enforces a workspace boundary.
- Path traversal attempts do not allow reading or writing outside the workspace.

## Explicitly deferred (out of scope)

- C-Trees research work and deeper engine scheduling semantics.
- Bundle slimming/optimization beyond basic knobs (size work is iterative).
- Signing / provenance / supply-chain hardening beyond checksums.

