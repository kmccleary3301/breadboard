# Install & Dev Quickstart (Phase 12)

This doc is the minimal “get productive quickly” path for maintainers working on **BreadBoard** (engine + TUI/CLI).

## Prereqs

- Node.js 20+
- Python 3.11+ (3.12 is also fine)

## Build the CLI/TUI (`breadboard`)

From the repo root:

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run build
```

### Local `breadboard` wrapper install

The `tui_skeleton` build runs a `postbuild` step that installs a small wrapper script named `breadboard` into a local bin directory.

Defaults:
- Linux/macOS: `~/.local/bin/breadboard`
- Windows: `~/.breadboard/bin/breadboard.cmd` (and `breadboard.ps1`)

Overrides:
- `BREADBOARD_BIN_DIR=/some/path` (relative paths are resolved from `tui_skeleton/`)
- `BREADBOARD_SKIP_LOCAL_BIN_INSTALL=1` to skip wrapper installation (recommended for CI)

## Run (local engine auto-start)

The CLI will auto-start a local engine if:
- Base URL is local (`localhost/127.0.0.1`) and reachable checks fail, and
- The command is not `connect`, `engine`, or `config`.

Common commands:

```bash
breadboard doctor --config agent_configs/opencode_mock_c_fs.yaml
breadboard ui --config agent_configs/opencode_mock_c_fs.yaml
breadboard run --config agent_configs/opencode_mock_c_fs.yaml "Say hi and exit."
```

Useful env toggles:
- `RAY_SCE_LOCAL_MODE=1` (skip Ray init during engine startup)
- `BREADBOARD_ENGINE_KEEPALIVE=1` (don’t auto-shutdown engine on CLI exit)
- `BREADBOARD_PROTOCOL_STRICT=1` (fail if engine protocol mismatches CLI)
- `BREADBOARD_KEYCHAIN=1` (if available, allow API token lookup from OS keychain)
- `BREADBOARD_SANDBOX_DRIVER=light|docker|process` (override sandbox driver selection)
- `RAY_USE_DOCKER_SANDBOX=1` (legacy toggle; forces docker sandbox if available)
- `BREADBOARD_DOCKER_NETWORK=<name>` (optional docker network name)
- `BREADBOARD_EVENTLOG_DIR=~/.breadboard/data/sessions` (optional JSONL event log root)
- `BREADBOARD_EVENTLOG_CANONICAL_ONLY=1` (record only canonical events in the JSONL event log)
- `BREADBOARD_EVENTLOG_MAX_MB=0` (optional cap; if >0, event log appends stop once size exceeds cap)
- `BREADBOARD_EVENTLOG_BOOTSTRAP=1` (preload session summaries from JSONL on engine start)
- `BREADBOARD_EVENTLOG_REPLAY=1` (also hydrate replay buffer from JSONL when bootstrapping)
- `BREADBOARD_SESSION_INDEX=1` (write session summaries to a JSON index)
- `BREADBOARD_SESSION_INDEX_DIR=...` (override session index root; defaults to event log dir)
- `BREADBOARD_SESSION_INDEX_ENGINE=json|sqlite` (index backend; default `json`)
- `BREADBOARD_SESSION_INDEX_READ=1` (allow /sessions to fall back to index when registry is empty)
- `BREADBOARD_SESSION_INDEX_BOOTSTRAP=1` (preload registry from session index on startup)
- `BREADBOARD_EVENTLOG_ENRICH=1` (use event logs to enrich index bootstrap status/timestamps)

Event log + index notes:
- Event logging is **off by default** and only activates when `BREADBOARD_EVENTLOG_DIR` is set.
- Bootstrap/replay flags are safe to use in dev; they do not alter session execution.

See also:
- `docs/ENGINE_PERSISTENCE_CONFIG.md`
- `docs/SESSION_PERSISTENCE_STRATEGY.md`

Optional keychain credentials:
```bash
breadboard auth set "<token>"
breadboard auth status
breadboard auth clear
```

## Packaging smokes (local)

### “Live” smoke (source engine)

```bash
bash scripts/phase12_live_smoke.sh
```

### npm pack smoke (cross-platform)

```bash
node scripts/cli_pack_smoke.mjs
```

## Running tests

Python:
```bash
python -m pytest -q tests/test_cli_bridge_contract_exports.py
```

TUI:
```bash
npm -C tui_skeleton test
```
