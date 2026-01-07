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

## Packaging smokes (local)

### “Live” smoke (source engine)

```bash
bash scripts/phase12_live_smoke.sh
```

### Bundled engine smoke (developer validation)

This uses a gitignored local bundle at `local_engine_bundles/dist/manifest.json`:

```bash
bash scripts/phase12_bundled_engine_smoke.sh
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

