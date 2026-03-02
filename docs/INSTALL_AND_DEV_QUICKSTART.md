# Install & Dev Quickstart (Phase 12)

This doc is the minimal “get productive quickly” path for maintainers working on **BreadBoard** (engine + TUI/CLI).

## Prereqs

- Node.js 20+
- Python 3.11+ (3.12 is also fine)

## Canonical local Python/engine path

After bootstrap, use the repo-local interpreter:

```bash
./.venv/bin/python
```

Launch engine directly from source with that interpreter:

```bash
./.venv/bin/python -m agentic_coder_prototype.api.cli_bridge.server
```

## One-command first-time setup (recommended)

From the repo root:

```bash
bash scripts/dev/bootstrap_first_time.sh
```

Optional readiness summary:

```bash
python scripts/dev/quickstart_first_time.py --include-advanced
make cli-capabilities
```

If the CLI wrapper is already available:

```bash
breadboard quickstart --include-advanced
```

Shortcut (Linux/macOS):

```bash
make help
make setup
make setup-fast
make setup-engine
make setup-fast-engine
make setup-refresh-python
make setup-tui
make setup-all
```

If the CLI wrapper is already installed, the same setup flow is available as:

```bash
breadboard setup --smoke
breadboard setup --sdk-hello-live
breadboard setup --all-checks
breadboard setup --profile engine
breadboard setup --profile tui
```

If `tui_skeleton` sources are intentionally absent in this checkout, use:

```bash
bash scripts/dev/bootstrap_first_time.sh --profile engine
```

Options:

```bash
# Include unit-only advanced smoke lane
bash scripts/dev/bootstrap_first_time.sh --smoke

# Recreate .venv from scratch
bash scripts/dev/bootstrap_first_time.sh --recreate-venv

# Force reinstall Python dependencies
bash scripts/dev/bootstrap_first_time.sh --refresh-python-deps

# Skip one side if you only need engine or only need TUI
bash scripts/dev/bootstrap_first_time.sh --skip-node
bash scripts/dev/bootstrap_first_time.sh --skip-python

# Profile shortcuts
bash scripts/dev/bootstrap_first_time.sh --profile engine
bash scripts/dev/bootstrap_first_time.sh --profile tui

# Full confidence pass (unit smoke + live sdk verification)
bash scripts/dev/bootstrap_first_time.sh --all-checks
```

Bootstrap is incremental on repeat runs:
- Python dependency reinstall is skipped when `requirements.txt` + interpreter + resolver are unchanged.
- `npm ci` is skipped when `package-lock.json` + Node version are unchanged.
- Node builds are skipped when `dist` outputs are newer than sources.

Doctor directly:

```bash
python scripts/dev/first_time_doctor.py --strict
python scripts/dev/first_time_doctor.py --profile engine --strict
```

If your CLI build supports first-time doctor flags:

```bash
breadboard doctor --first-time
breadboard doctor --first-time --first-time-profile engine
breadboard doctor --first-time --first-time-profile tui
```

SDK hello smokes (engine must be running):

```bash
python scripts/dev/python_sdk_hello.py
node scripts/dev/ts_sdk_hello.mjs
```

One-command live SDK verification (starts temporary local engine):

```bash
bash scripts/dev/sdk_hello_live_smoke.sh
# or
make sdk-hello-live
make onboarding-contract
# onboarding path smoke
make devx-smoke
# engine-only onboarding smoke (faster while TUI changes)
make devx-smoke-engine
make devx-smoke-live
# one-command full confidence pass + report
make devx-full-pass
# summarize latest full-pass timing report
make devx-timing
# engine-only variant while TUI is in flux
make devx-full-pass-engine
# inspect ~/.breadboard footprint and cleanup plan (dry-run)
make disk-report
# optional stricter TUI-inclusive checks
make doctor-full
make doctor-tui
# repair local CLI wrapper if broken
make repair-cli
```

Engine-only verification (skip TypeScript lane):

```bash
bash scripts/dev/sdk_hello_live_smoke.sh --no-ts
```

## Build the CLI/TUI (`breadboard`)

From the repo root:

```bash
npm -C tui_skeleton ci
npm -C tui_skeleton run build
# or
make repair-cli
```

For an even shorter first-time path, see: `docs/quickstarts/FIRST_RUN_5_MIN.md`.

### Local `breadboard` wrapper install

The `tui_skeleton` build runs a `postbuild` step that installs a small wrapper script named `breadboard` into a local bin directory.

Defaults:
- Linux/macOS: `~/.local/bin/breadboard`
- Windows: `~/.breadboard/bin/breadboard.cmd` (and `breadboard.ps1`)

Overrides:
- `BREADBOARD_BIN_DIR=/some/path` (relative paths are resolved from `tui_skeleton/`)
- `BREADBOARD_SKIP_LOCAL_BIN_INSTALL=1` to skip wrapper installation (recommended for CI)

If you rebuilt and still see old CLI behavior, refresh shell command hashing:

```bash
hash -r
```

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

Auth shortcuts:
```bash
breadboard auth set "<token>"
breadboard auth status
breadboard auth clear
breadboard auth list
```

Interactive provider login (OpenCode-style menu):
```bash
breadboard auth
breadboard auth login --config agent_configs/codex_cli_gpt51mini_e4_live.yaml
```
In the OpenAI login-method menu, use `ChatGPT Pro/Plus (browser, force re-login)` to force a fresh browser auth handshake even if a reusable local Codex token already exists.

Codex subscription direct flow (non-interactive / CI-friendly):
```bash
breadboard auth codex-subscription login --no-browser --config agent_configs/codex_cli_gpt51mini_e4_live.yaml
```

Notes:
- If the local keychain is locked/unavailable, BreadBoard saves subscription tokens to `~/.breadboard/auth/codex_subscription_tokens.json`.
- Auth attach will auto-start a local engine when `baseUrl` is local and unreachable.

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

Switcher-style SDK/engine smoke (advanced competitor-like use cases):
```bash
# Unit-only advanced migration checks
./scripts/smoke_switcher_fancy_use_cases.sh --no-live

# Full local live pass: python sdk + ts sdk
./scripts/smoke_switcher_fancy_use_cases.sh

# Optional tiny real-provider lane (OpenAI key loaded from .env if unset)
./scripts/smoke_switcher_fancy_use_cases.sh --live-provider

# Optional toggles
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-ts
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-auth
./scripts/smoke_switcher_fancy_use_cases.sh --no-live-provider
```
Notes:
- The script auto-selects a free localhost port to avoid collisions.
- The script includes a live provider-auth contract lane (400 + 409 + successful attach/detach).
- The live-provider lane is budget-minimal (`max_steps=1`, no-tool prompt).

## Disk maintenance (`~/.breadboard`)

If local engine artifacts/logging grow too large, use the retention-safe prune tool.

Dry-run (default):

```bash
python scripts/prune_breadboard_home.py
# or
make disk-report
```

Apply cleanup:

```bash
python scripts/prune_breadboard_home.py --apply
# or
make disk-prune
```

Common options:
- `--keep-name local-dev` (default keep)
- `--keep-latest 1` (keep most recent version dir)
- `--remove-tarballs-in-kept` (also remove `*.tar.gz` from kept dirs)
- `--json-out artifacts/maintenance/prune_breadboard_home.json`
