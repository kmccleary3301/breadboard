# Legacy TUI contract harness

This Ink client is retained for BreadBoard engine contract, replay, projection, and golden validation. It is not the product TUI and does not own the `bb` or `breadboard` commands. The canonical end-user TUI is [`kmccleary3301/breadboard-tui`](https://github.com/kmccleary3301/breadboard-tui).

## Stack

- `@effect/cli` for command routing and flag parsing
- `Ink` + `react` for the multi-pane REPL interface
- Shared HTTP/SSE client (`fetch`, `eventsource-parser`) layered on the Effect runtime

## Validation coverage

- HTTP/SSE engine contracts and resume behavior.
- Session, file, artifact, and projection fixtures.
- Ink transcript, layout, and interaction goldens.
- Deterministic tmux and terminal replay scenarios.

## SSE Event Contract (Engine)

- SSE `id:` uses the engine `seq` field (monotonic integer for ordering/resume).
- Event envelope includes `seq`, `timestamp_ms` (ms since epoch), and `protocol_version`.
- Resume uses `Last-Event-ID` = `seq`. If the resume window is exceeded, the engine returns `409` with `detail.code = "resume_window_exceeded"`.

## Development Workflow

```bash
cd tui_skeleton
npm install

# Run specific commands in dev mode
npm run dev -- ask "list repo modules"
npm run dev -- repl --config agent_configs/misc/opencode_openrouter_grok4fast_cli_default.yaml
npm run dev -- render <session-id> --output json

# Compile the contract harness
npm run build

# Run it directly when validating a contract
npm start -- --help
```

## Testing

```bash
npm run typecheck
npm test
```

Vitest coverage includes streaming logic, render section helpers, diff summarisation, and session cache persistence. Ink snapshot tests still emit a benign `TextInput` warning while rendering headless layouts.

## Interactive Harness (tmux + color capture)

```bash
# Start a fixed-size tmux session running the CLI.
bash ../scripts/start_tmux_repl.sh --cli breadboard --session breadboard-live --cols 120 --rows 36

# Send keystrokes programmatically (input bus).
node ../scripts/agent_input_bus.ts --session breadboard-live --keys "/help"

# Capture the tmux pane with full ANSI color fidelity (PNG + .ansi/.txt sidecars).
python ../scripts/tmux_capture_to_png.py --target breadboard-live:0 --scale 0.75
```

## TUI Configuration (env)

- `BREADBOARD_TUI_KEYMAP=claude|codex` — key binding set.
- `BREADBOARD_TUI_CHROME=claude|codex` — chrome style (prompt separators, `/` list density). Defaults to the keymap.
- `BREADBOARD_TUI_FILE_PICKER_RESOURCES_PATH=/path/to/resources.json` — optional @‑picker resources list.
- `BREADBOARD_TUI_FILE_PICKER_RESOURCES_JSON='[{\"label\":\"...\",\"detail\":\"...\"}]'` — inline resources list.
