# Live Workflow

## Setup

- baseUrl: `http://127.0.0.1:4173`
- mock bridge API endpoints active
- create session task: `run standard workflow`

## Steps

1. Create and attach a new session.
2. Verify stream state transitions to active (`connecting` or `streaming`).
3. Send a user message and verify input clears.
4. Resolve pending permission request via `Allow Once`.
5. Restore active checkpoint.
6. Open file preview for `README.md`.
7. Download artifact id `artifact-1.log`.
8. Verify audit log records:
   - `permission.decision`
   - `checkpoint.restore`
   - `artifact.download`
