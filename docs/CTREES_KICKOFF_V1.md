# C-Trees Kickoff (Pre-Work Checklist)

This doc captures the minimal plan to start C-Trees work without disrupting the parity baseline.

## Goals (MVP)

1) **Contract first**: Engine and TUI speak the same event shapes (`ctree_snapshot`, `ctree_node`, `task_event` metadata).
2) **Visible proof**: A synthetic PTY case renders a C-Tree summary in the tasks panel.
3) **Safe gating**: C-Tree UI can be toggled off by default and enabled on the breadboard profile.
4) **Non-invasive**: C-Tree events should not affect existing Claude/Codex parity flows.

## Non-goals (for now)

- Full interactive tree view or branch visualizer.
- Advanced tree editing or branch switching.
- Automated evaluation/metrics pipeline.

## Dependencies

- Engine event envelope v1.
- `ctree_snapshot` / `ctree_node` events.
- Optional `GET /sessions/{id}/ctrees` endpoint.
- Mock SSE test stream (synthetic) for the harness.

## Current Baseline (Pre-C-Trees)

- Parity baseline tagged: `pre_ctrees_parity_2026-01-06`.
- Latest mock + live stress bundles archived in `local_captures/pre_ctrees_parity_20260106/`.
- C-Tree mock PTY case already present: `tui_skeleton/scripts/ctree_summary_pty.json`.
- Mock SSE script already present: `tui_skeleton/scripts/mock_sse_ctree.json`.

## MVP Acceptance Criteria

- C-Tree summary line appears in tasks panel when enabled.
- C-Tree node id appears in task footer when provided.
- No new flicker or resize anomalies in stress runs.
- C-Tree UI is hidden by default on non-breadboard profiles.

## Next Steps

1) Wire engine to emit `ctree_snapshot` / `ctree_node` with stable payloads.
2) Ensure task events include `ctree_node_id` where relevant.
3) Run mock + live PTY harnesses and confirm stable output.
4) Expand to branch switcher and visual tree view (later phase).
