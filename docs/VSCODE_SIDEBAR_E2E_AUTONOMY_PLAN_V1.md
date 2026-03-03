# VSCode Sidebar E2E Autonomy Plan (V1)

Last updated: `2026-02-22`

## Purpose

Define a deterministic, artifact-rich E2E stack that lets BreadBoard's coding agent validate and close the VSCode/Cursor sidebar loop with minimal human intervention.

This plan is intentionally aligned with:

1. `docs_tmp/E2E Agent Tooling and Testing Manifesto.md`
2. `docs/VSCODE_SIDEBAR_ADRS_V1.md`
3. `docs/VSCODE_SIDEBAR_RPC_CONTRACT_V1.md`
4. `docs/VSCODE_SIDEBAR_EVENT_NORMALIZATION_V1.md`

## Principles

1. Determinism-first: state/contract checks are the core gate.
2. Engine contracts stay canonical; UI remains an adapter.
3. Artifacts are mandatory on failure.
4. Agent ergonomics matter: one command should yield a high-signal summary.
5. Security posture remains explicit (test mode gating, workspace safety, no secret leakage).

## Architecture (V1)

1. `bb-ide-e2e` runner CLI (in `vscode_sidebar/scripts/ide_e2e.mjs`) as the primary control entrypoint.
2. Scenario DSL with schema-backed validation (`docs/contracts/vscode_sidebar/e2e_scenario.schema.json`).
3. Tiered evidence model:
   1. Tier 0: reducer/projection/contract unit tests.
   2. Tier 1: host + real engine deterministic smoke.
   3. Tier 2: editor-real webview interactions (next tranche).
   4. Tier 3: fault injection and reconnect/gap chaos (next tranche).

## Implementation Status (P0-P3)

Plan status: `COMPLETE`

### P0/P1 complete

1. Canonical plan document + schema contract published.
2. Scenario pack and suite manifests introduced.
3. Structured artifact outputs introduced:
   - `meta.json`
   - `timeline.jsonl`
   - `summary.json`
   - `summary.md`

### P2 complete

1. Test-gated extension-host command API for editor-real orchestration:
   - `breadboard.sidebar.e2e.createSession`
   - `breadboard.sidebar.e2e.attachSession`
   - `breadboard.sidebar.e2e.sendMessage`
   - `breadboard.sidebar.e2e.sendCommand`
   - `breadboard.sidebar.e2e.stopSession`
   - `breadboard.sidebar.e2e.deleteSession`
   - `breadboard.sidebar.e2e.getObservation`
   - `breadboard.sidebar.e2e.webviewRequest`
   - `breadboard.sidebar.e2e.waitFor`
2. Host↔webview bridge protocol primitives added:
   - Host emits `e2e_req`
   - Webview responds with `e2e_res`
3. Webview supports deterministic E2E operations:
   - `ping`
   - `snapshot`
   - `setComposer`
   - `switchTab`
   - `setInput`
   - `click`
   - `getElementText`
4. E2E mode safety gate added:
   - enabled in `ExtensionMode.Test`, or
   - when `BREADBOARD_E2E=1` and workspace marker `.breadboard_e2e_ok` exists.
5. VS Code extension-host harness lane added via `@vscode/test-electron`.
6. Cursor parity invocation path added:
   - `npm run ide-e2e:editor-host:cursor`
   - requires `BREADBOARD_CURSOR_EXECUTABLE` (or `CURSOR_EXECUTABLE`) for editor binary path.
   - requires `BREADBOARD_CURSOR_USE_VSCODE_TEST_ELECTRON=1` (guarded experimental lane).
   - validated executable path in this environment: `/usr/share/cursor/cursor` (desktop binary; not remote-cli shim).

### P3 complete

1. `bb-ide-e2e` runner upgraded from single-scenario scaffold to manifest-driven execution:
   - suite resolution via `vscode_sidebar/e2e/suites/*.json`
   - fallback suite filtering via `scenario.suite`
   - `--scenario-id` direct scenario targeting
2. Schema-aware scenario validation at runner startup:
   - required field checks
   - schemaVersion checks
   - unique scenario IDs
   - unique per-scenario step IDs
3. Per-scenario retries with configurable backoff:
   - `maxRetries`
   - `retryBackoffMs`
4. Expectation assertions from scenario contracts are now enforced by runner:
   - inline syntax (`metric op value`) in `assert`
   - optional explicit fields (`metric`, `op`, `value`)
5. Tier-3 host-controller core scenarios wired:
   - `reconnect_resume.core`
   - `gap_detect.core`
6. Aggregated suite rollup summary now includes:
   - per-scenario pass/fail
   - attempt counts
   - failure signatures
   - scenario-level artifact paths
7. Runner emits per-scenario status lines suitable for CI log scanning.
8. Summary assertion helper added:
   - `vscode_sidebar/scripts/assert_ide_e2e_summary.mjs`
   - verifies latest suite summary is clean (`failedCount == 0`, `ok == true`).

## Scenario Surface (V1 Schema)

Scenario pack root:

- `vscode_sidebar/e2e/scenarios/`

Required fields:

1. `schemaVersion`
2. `id`
3. `tier`
4. `editorTargets`
5. `suite`
6. `engine`
7. `steps`
8. `expects`

## Artifact Contract (Runner Output)

Default run directory:

- `docs_tmp/interfaces/ide_e2e_runs/run-<timestamp>-<suite>-<editor>/`

Mandatory files:

1. `meta.json`
2. `timeline.jsonl`
3. `summary.json`
4. `summary.md`

Failure signature format:

- `IDE_E2E::<editor>::<scenario_or_suite>::<step>::<error_code>`

Canonical error codes:

1. `E_ASSERT_FAILED`
2. `E_RUNNER_STEP_FAILED`
3. `E_UNREACHABLE`
4. `E_CURSOR_TEST_ELECTRON_GATE_DISABLED`
5. `E_CURSOR_EXECUTABLE_MISSING`
6. `E_CURSOR_EDITOR_HOST_FAILED`
7. `E_CURSOR_IPC_CONNECT_REFUSED`

## Commands

Run smoke suite:

```bash
cd vscode_sidebar
npm run ide-e2e:smoke
```

Run PR suite:

```bash
cd vscode_sidebar
npm run ide-e2e:pr
```

Run nightly suite:

```bash
cd vscode_sidebar
npm run ide-e2e:nightly
```

Run Cursor suites with explicit desktop binary:

```bash
cd vscode_sidebar
BREADBOARD_CURSOR_EXECUTABLE=/usr/share/cursor/cursor BREADBOARD_CURSOR_USE_VSCODE_TEST_ELECTRON=1 node scripts/ide_e2e.mjs run --suite pr --editor cursor --strict
BREADBOARD_CURSOR_EXECUTABLE=/usr/share/cursor/cursor BREADBOARD_CURSOR_USE_VSCODE_TEST_ELECTRON=1 node scripts/ide_e2e.mjs run --suite nightly --editor cursor --strict
```

Direct runner usage:

```bash
node scripts/ide_e2e.mjs run --suite smoke --editor vscode --strict
node scripts/ide_e2e.mjs run --suite editor-host --editor vscode --strict
node scripts/ide_e2e.mjs run --suite pr --editor vscode --strict
node scripts/ide_e2e.mjs run --scenario-id reconnect_resume.core --editor vscode --strict
```

Notes:

1. On Linux environments without `DISPLAY`, editor-host suite auto-wraps with `xvfb-run -a`.
2. Webview bridge assertions are exercised when the webview attaches in headless runs; command-surface and engine-flow checks remain required either way.

## CI Tier Split (Current)

1. PR/push gate:
   - `.github/workflows/ci.yml` job: `vscode_sidebar_e2e`
   - runs: `typecheck`, `test`, `ide-e2e:pr`
2. Nightly lane:
   - `.github/workflows/vscode-sidebar-e2e-nightly.yml`
   - runs `ide-e2e:nightly` for VS Code.
   - runs conditional Cursor nightly suite when binary path is available.
3. Summary assertions:
   - PR job asserts latest `pr/vscode` summary is clean.
   - nightly job asserts latest `nightly/vscode` summary is clean.
   - nightly Cursor lane asserts latest `nightly/cursor` summary when Cursor lane is enabled.

## Completion Notes

1. This plan's required scope is closed.
2. Optional future enhancements (visual diffs, additional chaos scenarios) are non-blocking and should be tracked as separate follow-on scope, not as remaining work for this plan.
