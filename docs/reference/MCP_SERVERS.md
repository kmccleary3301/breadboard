# MCP Servers (Engine + CLI Bridge)

BreadBoard supports MCP (Model Context Protocol) servers as an **optional** tool source.

## Overview

- MCP servers are configured in the **engine config YAML** under `mcp:`.
- When enabled, the engine can:
  - spawn MCP servers over **stdio**
  - list their tools
  - expose tools to the model under names like `mcp.<server>.<tool>`
  - optionally record/replay tool calls via a JSONL “tape”

This feature is optional and guarded:
- Engine dependency: `mcp` (installed in CI via `requirements_web.txt`)
- Runtime toggle: `mcp.enabled: true`

## Config shape

Minimal example:

```yaml
version: 2
workspace:
  root: .

mcp:
  enabled: true
  servers:
    - name: files
      command: node
      args: ["./path/to/server.js"]
      cwd: "{workspace}"
      env:
        FOO: bar
```

Supported fields per server entry (best-effort normalization):
- `name` (recommended)
- `command` (required for live servers)
- `args` (optional)
- `cwd` / `working_dir` / `workdir` (optional; supports `{workspace}` / `{home}` tokens)
- `env` (optional)

## Static tools vs live tools

For deterministic tests/replays you can also declare **static MCP tool schemas** without running a server:

```yaml
mcp:
  enabled: false
  tools:
    - name: mcp.files.echo
      description: Echo back input
      schema:
        type: object
        properties:
          text: { type: string }
        required: [text]
```

Static tools are useful for:
- replay baselines
- parity testing
- offline development

## Permissions

The permission broker assigns MCP tools to the `mcp` category. You can restrict them with:

```yaml
permissions:
  mcp:
    default: deny
    allow:
      - "mcp.files.*"
```

## Recording and replay (tapes)

If you want to record tool calls:

```yaml
mcp:
  enabled: true
  record_tape: true
```

This writes a JSONL tape (preferably under the run directory) when the engine is running in live mode.

To replay:

```yaml
mcp:
  enabled: false
  replay_tape: "{workspace}/.breadboard/mcp_tape.jsonl"
```

## Implementation notes

Key modules:
- `agentic_coder_prototype/mcp/manager.py` (stdio server manager + tool listing)
- `agentic_coder_prototype/mcp/tooling.py` (config parsing + tool schema conversion)

