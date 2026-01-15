# Phase 12 — Definition of Done (Server-first)

This document defines what “Phase 12 is done” means for BreadBoard’s server-first engine architecture.

## In scope

- The **TUI/CLI package** (`tui_skeleton/`) builds and runs as `breadboard`.
- The **engine** runs behind the CLI bridge HTTP/SSE contract.
- A user can run **doctor / ui / run** locally with reasonable defaults.
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

Engine bundling is **explicitly out of scope** for Phase 12 due to workstation stability constraints.
The supported deployment story is:
- run the engine from Python (`python -m agentic_coder_prototype.api.cli_bridge.server`)
- connect the CLI/TUI to a running engine (local or remote), optionally via SSH tunnels
- rely on Ray for concurrency/multi-agent work (with safe defaults)

### Sandbox baseline safety

- The default “light” sandbox enforces a workspace boundary.
- Path traversal attempts do not allow reading or writing outside the workspace.

## Explicitly deferred (out of scope)

- C-Trees research work and deeper engine scheduling semantics.
- Bundling/packaging the engine as a standalone executable.

