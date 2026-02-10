# Claude Capture Checklist

Use this checklist when capturing Claude Code reference frames for alignment comparisons.

- Terminal size set explicitly (match `terminal_cols` / `terminal_rows` in `meta.json`).
- Font and theme recorded in `meta.json`.
- Claude model recorded (e.g., `Haiku 4.5`).
- Use a clean session (no prior scrollback).
- Capture after stable milestones (tool result, collapse line, completion).
- Avoid transient overlays (menus, file picker, help) unless the scenario is specifically testing them.
- Save raw capture as `capture.txt` alongside `meta.json`.
- Run:
  - `tsx scripts/normalize_claude_capture.ts --input capture.txt`
  - `tsx scripts/claude_capture_to_reference.ts --input capture.txt --normalize --grid`
  - Verify `reference.txt` and `frame.grid.json` are created.

## Current Scenario IDs

- claude_write_patch_markdown
- claude_tool_call_collapsed
- claude_diff_truncation
- claude_diff_patch_preview
- claude_diff_patch_truncated
- claude_streaming_pending

After capture, run:

```
cd tui_skeleton
./scripts/run_claude_compare_report.sh
```
