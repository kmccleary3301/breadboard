# BreadBoard CLI

Agentic terminal client for the BreadBoard engine. The CLI streams live sessions, exposes file and artifact operations, and mirrors the Ink-based REPL used during development.

## Stack

- `@effect/cli` for command routing and flag parsing
- `Ink` + `react` for the multi-pane REPL interface
- Shared HTTP/SSE client (`fetch`, `eventsource-parser`) layered on the Effect runtime

## Core Features

- `breadboard ask`, `breadboard resume`, `breadboard sessions` — non-interactive workflows built on the shared streaming helper.
- `breadboard repl` — Ink TUI with conversation/tool panes, status bar, and slash commands (`/mode`, `/plan`, `/model`, `/test`, `/files`, `/remote`, `/status`, `/retry`, ...).
- File utilities: `breadboard files ls|cat|apply`. `cat` supports `--out` for local export; `apply` accepts `--diff`, `--diff-file <path>`, or `--diff-file -` (stdin) and summarizes affected files before applying.
- Artifact access plus enhanced transcript rendering via `breadboard render <session>` (text or `--output json`), including tool events and reward summaries.

## Development Workflow

```bash
cd tui_skeleton
npm install

# Run specific commands in dev mode
npm run dev -- ask "list repo modules"
npm run dev -- repl --config agent_configs/opencode_openrouter_grok4fast_cli_default.yaml
npm run dev -- render <session-id> --output json

# Compile the CLI
npm run build

# Link binary locally (optional)
npm link
breadboard --help
```

## Testing

```bash
npm run typecheck
npm test
```

Vitest coverage includes streaming logic, render section helpers, diff summarisation, and session cache persistence. Ink snapshot tests still emit a benign `TextInput` warning while rendering headless layouts.

## TUI Configuration (env)

- `BREADBOARD_TUI_KEYMAP=claude|codex` — key binding set.
- `BREADBOARD_TUI_CHROME=claude|codex` — chrome style (prompt separators, `/` list density). Defaults to the keymap.
- `BREADBOARD_TUI_FILE_PICKER_RESOURCES_PATH=/path/to/resources.json` — optional @‑picker resources list.
- `BREADBOARD_TUI_FILE_PICKER_RESOURCES_JSON='[{\"label\":\"...\",\"detail\":\"...\"}]'` — inline resources list.
