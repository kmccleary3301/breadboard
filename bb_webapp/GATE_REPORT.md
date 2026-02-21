# Gate Report

Date: 2026-02-21
Branch: `bb-webapp-v1-p0`

## Executed Gates

- `npm run gate:p0` ✅
- `npm run gate:p1p2` ✅
- `npm run gate:full` ✅

## Test Summary

- Full suite: `17` test files, `53` tests passing.
- Targeted P1/P2 suite: `7` test files, `21` tests passing.

## Build Summary

- Typecheck: passed.
- Vite production build: passed.
- Latest emitted assets:
  - `dist/index.html` (gzip ~0.41 kB)
  - `dist/assets/index-_x9EIu1h.js` (gzip ~64.89 kB)
  - `dist/assets/MarkdownMessage-DjBH_rO_.js` (gzip ~144.07 kB)

## Coverage Highlights

- Checkpoint flow smoke (`checkpointFlow.test.ts`).
- Task graph merge/index/rollup behavior (`taskGraph.test.ts`, `projection.test.ts`).
- Diff parser malformed/truncation safety (`diffParser.test.ts`).
- Permission command fallback behavior (`sessionCommands.test.ts`).
- Replay determinism and parity contract guards (`replayDeterminism.test.ts`, `contractsParity.test.ts`).
