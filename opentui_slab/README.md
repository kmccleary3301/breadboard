# BreadBoard OpenTUI slab (prototype)

Scrollback-first OpenTUI “bottom slab” UI:
- transcript stays in real terminal scrollback (stdout)
- OpenTUI owns only the bottom `experimental_splitHeight` region
- controller + UI are separate processes, connected via localhost TCP NDJSON

## Run

From repo root:

```bash
bash breadboard_repo/scripts/dev/run_opentui_phaseB.sh agent_configs/codex_cli_gpt51mini_e4_live.yaml
```

## Phase C UX (current)

Keybinds (default):
- `/` opens the commands palette when the composer is empty
- `Ctrl+K` commands palette
- `Alt/Option+P` model picker
- `@` file picker (inserts `@path` into the composer)
- `Ctrl+O` transcript search (best-effort over a bounded controller buffer)
- `Ctrl+P` / `Ctrl+N` composer history prev/next
- `Ctrl+R` UI restart (controller stays up; draft restored)
- `Ctrl+D` exit UI + controller

Model picker query:
- `provider:<needle>` filters providers (e.g. `provider:open gpt`)

File picker details:
- Item detail shows `size · large · binary/text` (best-effort).
- Selecting a `large`/`binary` file prints a `[file] note: ...` line to stdout.

## Useful env vars

- `BREADBOARD_WORKSPACE`: workspace root for file indexing.
- `BREADBOARD_FILE_PICKER_INDEX_NODE_MODULES`: set to `1` to index `node_modules/` (default off).
- `BREADBOARD_FILE_PICKER_INDEX_HIDDEN_DIRS`: set to `1` to index hidden dirs (default off).
- `BREADBOARD_FILE_PICKER_WARN_BYTES`: “large file” threshold for overlay labeling (default 1,000,000).
