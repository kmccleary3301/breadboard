# BreadBoard TUI Runbook

This runbook covers starting the CLI bridge, running the TUI, and exercising live/stress harnesses.

## 1) Start the CLI bridge (engine → TUI)

```bash
cd /shared_folders/querylake_server/ray_testing/ray_SCE
python -m agentic_coder_prototype.api.cli_bridge.server
```

Env vars (optional):

```bash
export BREADBOARD_CLI_HOST=127.0.0.1
export BREADBOARD_CLI_PORT=9099
export BREADBOARD_CLI_LOG_LEVEL=info
```

Health check:

```bash
curl http://127.0.0.1:9099/health
```

## 2) Run the TUI

```bash
cd tui_skeleton
npm run build
```

The build installs a local `breadboard` wrapper into `~/.local/bin` (or `BREADBOARD_BIN_DIR`).
Ensure that directory is on your `PATH`, then:

```bash
export BREADBOARD_API_URL=http://127.0.0.1:9099
# Use small/cheap live configs when hitting real models.
export BREADBOARD_DEFAULT_CONFIG=agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml

breadboard repl
```

If you prefer direct node execution:

```bash
export BREADBOARD_API_URL=http://127.0.0.1:9099
# Use small/cheap live configs when hitting real models.
export BREADBOARD_DEFAULT_CONFIG=agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml

node dist/main.js repl
```

## 2.1) Engine manager (bundles)

The CLI supports a bundle cache and simple engine management commands:

```bash
# Inspect cache + pinned version
breadboard engine status

# Download a bundle from a manifest URL and pin it
export BREADBOARD_ENGINE_MANIFEST_URL="https://example.com/engine/manifest.json"
breadboard engine update --pin

# Pin a specific version already cached
breadboard engine pin 1.0.0

# Use an explicit engine binary
breadboard engine use /path/to/breadboard-engine
```

Auto-download on first run (optional):

```bash
export BREADBOARD_ENGINE_MANIFEST_URL="https://example.com/engine/manifest.json"
export BREADBOARD_ENGINE_AUTO_DOWNLOAD=1
```

Compatibility enforcement (optional):

```bash
# Fail fast if protocol mismatches
export BREADBOARD_PROTOCOL_STRICT=1
```

## 2.2) Build a local engine bundle (single platform)

```bash
python scripts/build_engine_bundle.py \
  --version 1.0.0 \
  --engine-bin /path/to/breadboard-engine \
  --base-url https://cdn.example.com/breadboard/engine
```

This writes:
- `dist/engine_bundles/<version>/breadboard-engine-<platform>-<arch>.tar.gz` (or `.zip` on Windows)
- `dist/engine_bundles/manifest.json` (compatible with `BREADBOARD_ENGINE_MANIFEST_URL`)

## 3) Stress harness (mock SSE)

```bash
cd tui_skeleton
npx tsx scripts/run_stress_bundles.ts --case slash_menu --case model_picker --no-zip
```

Artifacts land in `tui_skeleton/artifacts/stress/<timestamp>/`.

## 4) Stress harness (live engine)

Run with a live CLI bridge already up:

```bash
cd tui_skeleton
npx tsx scripts/run_stress_bundles.ts --include-live --no-zip
```

Auto-start the CLI bridge (if `BREADBOARD_API_URL` is unreachable):

```bash
cd tui_skeleton
npx tsx scripts/run_stress_bundles.ts --include-live --auto-start-live --no-zip
```

You can also export:

```bash
export BREADBOARD_CLI_AUTO_START=1
```

Prefer mini-model configs for live runs:

```bash
export BREADBOARD_LIVE_CONFIG=../agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml
```

Deterministic permission modal for live harness runs:

```bash
export BREADBOARD_DEBUG_PERMISSIONS=1
```

Optional tmux capture during stress runs:

```bash
export BREADBOARD_TMUX_CAPTURE_TARGET="breadboard-live:0"
export BREADBOARD_TMUX_CAPTURE_SCALE=0.75
```

Visual baselines (untracked) live here:

```text
tui_skeleton/artifacts/visual_baselines/
```

## 5) tmux harness + color capture

```bash
# Start fixed-size tmux session with BreadBoard
bash scripts/start_tmux_repl.sh --cli breadboard --session breadboard-live --cols 120 --rows 36

# Send input programmatically
node scripts/agent_input_bus.ts --session breadboard-live --keys "/help"

# Capture tmux pane with ANSI colors (PNG + .ansi + .txt)
python scripts/tmux_capture_to_png.py --target breadboard-live:0 --scale 0.75

# Copy captures into the untracked baseline folder if you want to diff by hand
cp tui_skeleton/artifacts/stress/<timestamp>/tmux_capture_*.png tui_skeleton/artifacts/visual_baselines/
```

## 6) C-Trees UI toggles (optional)

The C-Tree UI is disabled by default unless using `breadboard_v1`. Enable explicitly:

```bash
export BREADBOARD_CTREES_ENABLED=1
export BREADBOARD_CTREES_SUMMARY=1
export BREADBOARD_CTREES_TASK_NODE=1
```

User config override (`~/.breadboard/config.json`):

```json
{
  "ctrees": {
    "enabled": true,
    "showSummary": true,
    "showTaskNode": true
  }
}
```
