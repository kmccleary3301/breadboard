# Plugin Security Notes (Phase 12)

BreadBoard’s plugin system is designed to be **data-first** and **non-executing** by default.

## Core principle

**Plugins must not cause arbitrary code execution** in the CLI/TUI or engine simply by being installed or discovered.

## What a plugin can contain today

In the current Phase 12 implementation, plugin content is limited to:
- `breadboard.plugin.json` (manifest metadata)
- Skill files (JSON/YAML) that contribute prompt/graph skills
- Optional MCP server declarations (data-only; server execution is still gated by engine config + permissions)
- Optional permission suggestions (subject to engine enforcement)
- Optional hook registrations (disabled by default; require explicit engine config)

## What we explicitly do NOT do

- No `eval()` / dynamic JS execution for plugins.
- No `npm install` / `pip install` / postinstall hooks during plugin install.
- No automatic execution of plugin-provided binaries.
- No loading “UI extensions” as JavaScript.

## Install safety (CLI)

`breadboard plugin install` currently performs a **directory copy** into:
- workspace scope: `{cwd}/.breadboard/plugins/<plugin-id>/`
- user scope: `~/.breadboard/plugins/<plugin-id>/`

It does not execute plugin code.

## Runtime safety (engine)

- Tool execution is still mediated by:
  - the permission broker (`permissions.*`)
  - policy packs (`policies.*`) for enterprise controls
- Plugins are assigned a trust level by the engine based on:
  - install location (workspace vs user)
  - explicit allow/deny IDs in config
  - config trust flags

Untrusted plugin hook tools are denied by default (configurable).

## Plugin hooks (opt-in)

Plugin hooks are **disabled by default**. To enable:
- `hooks.enabled: true`
- `hooks.allow_plugin_hooks: true`

By default, only **trusted** plugins are allowed to register hooks. To allow
untrusted plugins, set `hooks.allow_untrusted_plugins: true`.

## Future UI extensions

If/when UI extensions are implemented, they should be limited to **safe primitives**:
- JSON schema rendering
- markdown rendering
- static assets (icons)

If executable UI code becomes necessary, it must run inside a sandboxed environment with explicit trust + permissions.
