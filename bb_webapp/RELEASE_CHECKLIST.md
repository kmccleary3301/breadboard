# Webapp Release Checklist (P1/P2 Additions)

## Build and Test Gates

- [ ] `npm --prefix bb_webapp run gate:full` passes.
- [ ] CI `webapp_gate` job passes.
- [ ] Typecheck/build outputs are clean.

## Feature Verification

- [ ] Checkpoint list + restore flow succeeds end-to-end.
- [ ] Diff viewer handles large patches without UI lockup.
- [ ] Task tree updates on streamed task/ctree events.
- [ ] Permission ledger filters and revoke fallback messaging behave correctly.
- [ ] Transcript/artifact search navigation works.
- [ ] Replay export/import remains deterministic.

## Security Verification

- [ ] Remote mode only sends Authorization headers.
- [ ] Markdown unsafe-content fallback covers script/iframe/js-url payloads.
- [ ] Raw-event and replay export redaction checks pass.
- [ ] CSP header present in `index.html`.

## Docs and Ops

- [ ] README updated for new operator workflows.
- [ ] RUNBOOK updated for remote-safe operation.
- [ ] Gate report artifact captured for this release.
