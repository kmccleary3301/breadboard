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

export BREADBOARD_API_URL=http://127.0.0.1:9099
# Use small/cheap live configs when hitting real models.
export BREADBOARD_DEFAULT_CONFIG=agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml

node dist/main.js repl
```

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
