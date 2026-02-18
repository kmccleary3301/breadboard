# Safe Mode Execution Policy (Deletion-Crisis Guardrails)

This policy is mandatory for engine-side development in the canonical
`breadboard_repo/` workspace.

## Checklist (Instituted)

1. Do **not** run runtime/task commands directly against canonical repo unless preflight passes.
   - Blocked commands without preflight: `breadboard run`, `python main.py ...`, parity/replay task runs.
2. Limit canonical-repo work to docs/code edits and non-destructive verification.
3. If runtime validation is required, use an isolated disposable workspace clone/copy.
4. Run workspace safety preflight before any task run:

```bash
python scripts/preflight_workspace_safety.py --config <config.yaml>
```

## Failure Pattern Covered

This policy addresses the documented deletion-crisis class:

- dangerous `workspace.root` values (`.`, `..`, repo root, ancestor),
- cleanup code paths deleting workspace recursively.

See:

- `docs_tmp/DELETION_CRISIS_POST_MORTEM.md`
- `docs_tmp/DELETION_CRISIS_POST_MORTEM_20260124_ADDENDUM.md`

## Required Safe Defaults

- workspace roots must resolve under repo subdirectories (for example `agent_ws/...`)
  or under temp directories.
- repo root, repo ancestors, home directory, and temp root are forbidden as workspace roots.

## Operational Notes

- `scripts/preflight_workspace_safety.py` returns non-zero on unsafe roots.
- `agentic_coder_prototype/agent.py` contains matching runtime guards.
- `scripts/run_parity_replays.py` uses safe-delete rails and workspace validation.
