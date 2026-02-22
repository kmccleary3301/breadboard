# Webapp Release Checklist (P1/P2 Additions)

## Build and Test Gates

- [x] `npm --prefix bb_webapp run gate:full` passes.
- [x] CI `webapp_gate` job passes.
- [x] Typecheck/build outputs are clean.

## Feature Verification

- [x] Checkpoint list + restore flow succeeds end-to-end.
- [x] Diff viewer handles large patches without UI lockup.
- [x] Task tree updates on streamed task/ctree events.
- [x] Permission ledger filters and revoke fallback messaging behave correctly.
- [x] Transcript/artifact search navigation works.
- [x] Replay export/import remains deterministic.

## Security Verification

- [x] Remote mode only sends Authorization headers.
- [x] Markdown unsafe-content fallback covers script/iframe/js-url payloads.
- [x] Raw-event and replay export redaction checks pass.
- [x] CSP header present in `index.html`.

## Docs and Ops

- [x] README updated for new operator workflows.
- [x] RUNBOOK updated for remote-safe operation.
- [x] Gate report artifact captured for this release.
