# BreadBoard VSCode Terminal Harness

This extension is a V4 production-readiness harness for running BreadBoard inside a VSCode integrated-terminal surface. It is intentionally local-only and test-focused.

The extension auto-runs when these environment variables are present:

- `BREADBOARD_VSCODE_HARNESS_SCENARIO`: absolute path to a scenario JSON file.
- `BREADBOARD_VSCODE_HARNESS_ARTIFACT_DIR`: absolute path to the artifact output directory.
- `BREADBOARD_VSCODE_NODE_BIN`: optional Node binary for the external PTY sidecar; defaults to `node` from `PATH`.

## Architecture

The harness uses VSCode's `Pseudoterminal` API for integrated-terminal rendering and a local Node sidecar for the actual PTY:

- VSCode extension host owns the `Pseudoterminal` and forwards output to VSCode's terminal renderer.
- `src/ptyBridgeServer.cjs` runs under normal local Node, loads `node-pty`, spawns `bash`, and accepts input/resize/kill messages over a short Unix socket in `/tmp`.
- The extension records raw PTY bytes and mirrors them through `@xterm/headless` to export parsed buffer and viewport truth.
- The sidecar design avoids VSCode/Electron native-module ABI issues while preserving true PTY resize semantics.

## Evidence Scopes

Scenario assertions can target different evidence surfaces:

- `raw`: append-only PTY/plain stream, useful for host-history, prompt-submission, and leakage checks.
- `buffer`: parsed terminal buffer from `@xterm/headless`.
- `viewport`: parsed visible viewport from `@xterm/headless`, useful for composer/footer/landing visibility.

The parsed viewport/buffer artifacts are the current automated visual-equivalent lane for VSCode proxy testing. Pixel screenshots remain a possible future companion, but V4-B treats parsed terminal truth plus final manual Cursor smoke as the higher-value coverage path for text TUI correctness.

## Artifacts

Each run emits:

- `scenario.json`
- `vscode_info.json`
- `extension_info.json`
- `workspace_settings.json`
- `env.json`
- `pty_raw.ansi`
- `pty_plain.txt`
- `scrollback_final.txt`
- `viewport_final.txt`
- `terminal_state_final.json`
- `terminal_frames.ndjson`
- `visible_frames.md`
- `input_log.ndjson`
- `dimension_events.ndjson`
- `breadboard_artifacts/`
- `anomalies.json`
- `verdict.json`
- `summary.md`

`breadboard_artifacts/` currently includes state dumps and TUI debug streams when BreadBoard reaches the REPL path.
