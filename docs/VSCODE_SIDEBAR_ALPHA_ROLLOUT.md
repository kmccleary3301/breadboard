# VSCode Sidebar Alpha Rollout Checklist (V1)

Last updated: `2026-02-21`

## Purpose

Define entry/exit criteria and triage flow for internal alpha rollout of the BreadBoard VSCode/Cursor sidebar.

## Entry Criteria

- [ ] `npm run typecheck` passes in `vscode_sidebar`.
- [ ] `npm run test` passes in `vscode_sidebar`.
- [ ] Engine compatibility matrix reviewed for current build.
- [ ] Quickstart and troubleshooting docs published.
- [ ] Security baseline reviewed (token isolation, CSP, trust gating).

## Alpha Scope

1. Internal users only.
2. Primary workflows:
   1. create + attach session
   2. chat send/stop
   3. permission decisions
   4. file browse/snippet
   5. open diff

## Bug Triage Rubric

Severity:

1. `S0` Data loss or wrong target workspace execution.
2. `S1` Session control broken (cannot send/stop/attach reliably).
3. `S2` Degraded UX with workaround (missing cards, partial render issues).
4. `S3` Cosmetic/low impact.

Priority:

1. Blocker before beta: all `S0`, all `S1`.
2. Track for beta: high-frequency `S2`.
3. Defer: low-frequency `S2`, `S3`.

## Exit Criteria (Alpha -> Beta Candidate)

- [ ] No open `S0` defects.
- [ ] No open `S1` defects.
- [ ] Reconnect/resume behavior validated across repeated forced disconnects.
- [ ] Permission flow validated across at least one full run.
- [ ] Cursor smoke pass recorded.
