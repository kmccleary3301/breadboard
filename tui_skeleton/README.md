# BreadBoard CLI

Agentic terminal client for the BreadBoard engine. The CLI streams live sessions, exposes file and artifact operations, and mirrors the Ink-based REPL used during development.

## Stack

- `@effect/cli` for command routing and flag parsing
- `Ink` + `react` for the multi-pane REPL interface
- Shared HTTP/SSE client (`fetch`, `eventsource-parser`) layered on the Effect runtime

## Core Features

- `kyle ask`, `kyle resume`, `kyle sessions` — non-interactive workflows built on the shared streaming helper.
- `kyle repl` — Ink TUI with conversation/tool panes, status bar, and slash commands (`/mode`, `/plan`, `/model`, `/test`, `/files`, `/remote`, `/status`, `/retry`, ...).
- File utilities: `kyle files ls|cat|apply`. `cat` supports `--out` for local export; `apply` accepts `--diff`, `--diff-file <path>`, or `--diff-file -` (stdin) and summarizes affected files before applying.
- Artifact access plus enhanced transcript rendering via `kyle render <session>` (text or `--output json`), including tool events and reward summaries.

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
kyle --help
```

## Testing

```bash
npm run typecheck
npm test
```

Vitest coverage includes streaming logic, render section helpers, diff summarisation, and session cache persistence. Ink snapshot tests still emit a benign `TextInput` warning while rendering headless layouts.
