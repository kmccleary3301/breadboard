# Phase5 Known Risks

## Shared-session overlay contamination

When multiple replay scenarios are run back-to-back in a single tmux session, an overlay from one scenario can carry into the next scenario's startup window and trigger preflight failures.

Current mitigation:
- Run parity-signoff scenarios in isolated fresh sessions (one session per scenario).

Non-impact scope:
- Does not block per-scenario parity gates used for hard-gate/nightly contract validation.
