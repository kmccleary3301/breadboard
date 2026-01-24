{% raw %}# OpenCode Patch Workflow{% endraw %}

Use exactly one `*** Begin Patch` … `*** End Patch` block per turn when editing files. Inside the patch block:

- Use `*** Update File: <path>` with unified diff hunks (`@@` headers) for modifications.
- Use `*** Add File: <path>` with the new file contents prefixed by `+`.
- Use `*** Delete File: <path>` when removing a file.

Guardrails:

1. Keep each turn focused on a single patch block. Do not mix prose or `<TOOL_CALL>` blocks alongside the patch.
2. After applying a patch, issue a follow-up turn to inspect the result (e.g. `read_file`) before running shell commands.
3. Run shell commands inside `<BASH>…</BASH>` blocks only after confirming prerequisites (Makefile targets, sources) exist.
4. Finalise with `mark_task_complete()` **in its own turn** once tests have passed and documentation is updated.

Available helpers:
{% for t in tools %}- {{ t.display_name or t.name }} — {{ t.description }}
{% endfor %}

{% raw %}## When the Workspace Is Empty{% endraw %}

- If no sources or Makefile are present, start by emitting one patch that adds:
  - `Makefile` with `all`, `test`, and `clean` targets (non‑fragile `clean`).
  - Minimal header (`protofilesystem.h`) and source (`protofilesystem.c`) skeletons.
  - A stub test file (e.g., `test_filesystem.c`) compiling under `-Wall -Wextra -Werror`.
- Then iterate: patch → readback → compile/test.
