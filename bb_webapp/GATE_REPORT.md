# Gate Report

Date: 2026-02-21
Branch: `bb-webapp-v1-p0`

## Executed Gates

- `npm run gate:p0` ✅
- `npm run gate:p1p2` ✅
- `npm run gate:full` ✅

## Test Summary

- Full suite: `20` test files, `63` tests passing.
- Targeted P1/P2 suite: `7` test files, `24` tests passing.

## Build Summary

- Typecheck: passed.
- Vite production build: passed.
- Latest emitted assets:
  - `dist/index.html` (gzip ~0.41 kB)
  - `dist/assets/index-BqOhvz8e.js` (gzip ~64.93 kB)
  - `dist/assets/MarkdownMessage-Clf3v-l5.js` (gzip ~144.07 kB)

## Coverage Highlights

- Checkpoint flow smoke (`checkpointFlow.test.ts`).
- Checkpoint restore payload normalization against bridge event shape (`checkpoints.test.ts`).
- Task graph merge/index/rollup behavior (`taskGraph.test.ts`, `projection.test.ts`).
- Diff parser malformed/truncation safety (`diffParser.test.ts`).
- Search navigation index/fallback anchor resolution (`searchNavigation.test.ts`).
- Canonical command parity and fallback behavior (`sessionCommands.test.ts`, `contractsParity.test.ts`).
- Permission ledger filter semantics (`permissionLedgerFilter.test.ts`).
- CSP presence/hardening directives in entry HTML (`csp.test.ts`).
- Replay determinism and parity contract guards (`replayDeterminism.test.ts`, `contractsParity.test.ts`).
