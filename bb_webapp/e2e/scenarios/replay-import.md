# Replay Import Hydration

## Setup

- baseUrl: `http://127.0.0.1:4173`
- mock bridge API endpoints (`health`, `status`, `models`, `sessions`)
- replay fixture: `e2e/fixtures/replay_import.json`

## Steps

1. Navigate to `/`.
2. Verify heading `BreadBoard Webapp V1 (P0 Scaffold)` is visible.
3. Upload replay fixture via hidden file input (`data-testid=replay-import-input`).
4. Verify transcript shows:
   - `Summarize release readiness.`
   - `Verification complete.`
5. Verify tools panel includes `write_file` and a visible `Diff Viewer`.
6. Verify checkpoint row `Before release` is rendered.
7. Verify permission ledger includes `run_command`.
8. Verify task tree includes `Run verification`.
9. Search for `verification` and verify at least one result is visible.
