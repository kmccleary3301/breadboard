# Log Reduction CLI Manual

The `scripts/log_reduce.py` CLI summarizes BreadBoard logging runs without flooding
context. It prints a compact directory tree, per-turn summaries, tool activity,
and optionally the original diffs.

## Basic Usage

```bash
python scripts/log_reduce.py <log_dir>
```

- `<log_dir>` points to a run directory under `logging/`, e.g.
  `logging/20250925-233436_agent_ws_opencode`.
- By default the tool shows a shallow directory tree (depth 2) followed by the
  first 25 turns of conversation with truncated content.

## Key Options

| Option | Description |
| --- | --- |
| `--max-length` | Maximum characters per line (default 120). |
| `--max-lines` | Maximum lines shown per content block before truncation (default 25). |
| `--max-depth` | Tree traversal depth (default 2). |
| `--turn-limit` | Number of turns to display; omit for all turns. |
| `--include-roles` | Comma-separated roles to include (e.g. `assistant,user`). |
| `--skip-roles` | Comma-separated roles to exclude (e.g. `system`). |
| `--tool-only` | Print only per-turn summaries, tool call previews, and tool results. |
| `--show-diffs` | Show raw diff content instead of summarised `patch add/update/delete` lines. |
| `--skip-tree` | Skip directory tree output. |
| `--skip-conversation` | Skip conversation summary entirely. |

## Example Commands

1. **Default view with 5 turns**
   ```bash
   python scripts/log_reduce.py logging/<latest> --turn-limit 5
   ```

2. **Compact tool timeline**
   ```bash
   python scripts/log_reduce.py logging/<latest> --tool-only --turn-limit 20
   ```

3. **Assistant-only view with full diffs**
   ```bash
   python scripts/log_reduce.py logging/<latest> \
       --include-roles assistant --show-diffs --turn-limit 3
   ```

4. **Suppress prompts, focus on user + tool results**
   ```bash
   python scripts/log_reduce.py logging/<latest> \
       --skip-roles system --max-length 80 --max-lines 10
   ```

## Output Highlights

- **Directory Tree**: Shows top-level folders and file sizes (depth controlled by `--max-depth`).
- **Turn Summary**: Each turn prints `finish`, tool call count/names, tool result count,
  validation error flag, and token usage (when available). Turns with identical
  system/user prompts collapse to `role=(same as previous)`.
- **Patches**: By default rendered as `patch add/update/delete <file>`. Use `--show-diffs`
  for the raw diff content.
- **Tool Synopsis**: Aggregated at the end, listing how many turns invoked each tool,
  combining provider-native calls and textual tool call blocks (e.g. `<TOOL_CALL>`, `<BASH>`).

## Tips

- Keep `--max-lines` low (e.g. 6–8) when inspecting long runs to avoid triggering
  the truncated block warnings. Increase temporarily when verifying longer context.
- Combine `--tool-only` with a high `--turn-limit` to scan for validation errors or
  repeated tool calls quickly.
- Pipe output to a log for later reference:
  ```bash
  python scripts/log_reduce.py logging/<latest> --tool-only > scripts/logreduce_toolonly_latest.log
  ```

## Future Enhancements (ideas)

- Parse `<PATCH_RESULT>` summaries to list success/failure counts per tool.
- Summarise provider-native tool calls if future runs emit them separately.
- Add `--latest` shortcut to automatically target the newest run directory.

Keep this manual close when you revisit log analysis; the CLI’s knobs make it
useful both for quick triage and deep diff inspection.
