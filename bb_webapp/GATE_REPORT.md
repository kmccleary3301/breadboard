# Gate Report

Date: 2026-02-22
Branch: `bb-webapp-v1-p0`
Head: `8c596bbd62c26a584a48e5a6980cb743fec43c64`

## Executed Gates

- `npm run gate:p0` ✅
- `npm run gate:p1p2` ✅
- `npm run gate:full` ✅
- `npm run check:backend-commands` ✅
- `npm run verify:csp-dist` ✅

## Test Summary

- Full suite: `20` test files, `66` tests passing.
- Targeted P1/P2 suite: `7` test files, `25` tests passing.
- CLI bridge smoke set: `4` test files (`operator_http`, `permission_revoke`, `checkpoint_commands`, `permission_rule_persistence`) all passing.

## Build Summary

- Typecheck: passed.
- Vite production build: passed.
- Latest emitted assets:
  - `dist/index.html` (gzip ~0.41 kB)
  - `dist/assets/index-dTSMgtf2.js` (gzip ~64.93 kB)
  - `dist/assets/MarkdownMessage-xJCbzasp.js` (gzip ~144.07 kB)

## CI Evidence

- Previous failing run (contract drift baseline): `22266864742`
: `https://github.com/kmccleary3301/breadboard/actions/runs/22266864742`
- Intermediate rerun (same blocker isolated): `22267408115`
: `https://github.com/kmccleary3301/breadboard/actions/runs/22267408115`
- Final passing run (branch head `8c596bb`): `22267459252`
: `https://github.com/kmccleary3301/breadboard/actions/runs/22267459252`
- Final pass job links:
: `Webapp P1/P2 gate (job 64416198403)` and `Python (ubuntu) (job 64416198405)`
- Final run artifacts:
: `tui-goldens-ubuntu-latest` (`5604163018`),
: `runtime-gates-ubuntu-latest` (`5604163100`)

## Contract + Security Checks

- CLI bridge command inventory check is enforced by `bb_webapp/scripts/verify-backend-command-inventory.mjs`.
- Dist CSP verification is enforced by `bb_webapp/scripts/verify-dist-csp.mjs`.
- Replay export now redacts sensitive payload keys at package-build time (`replayPackage.ts` + `replayPackage.test.ts`).
- Nested redaction coverage expanded (`redaction.test.ts`).
- Non-canonical checkpoint command aliases were scanned and remain absent from command emit paths.

## Coverage Highlights

- Operator endpoint smoke (`tests/test_cli_bridge_operator_http_smoke.py`).
- Local bridge permission revoke smoke (`tests/test_cli_bridge_permission_revoke_smoke.py`).
- Checkpoint flow smoke (`checkpointFlow.test.ts`).
- Checkpoint restore payload normalization against bridge event shape (`checkpoints.test.ts`).
- Task graph merge/index/rollup behavior (`taskGraph.test.ts`, `projection.test.ts`).
- Diff parser malformed/truncation safety (`diffParser.test.ts`).
- Search navigation index/fallback anchor resolution (`searchNavigation.test.ts`).
- Canonical command parity and fallback behavior (`sessionCommands.test.ts`, `contractsParity.test.ts`).
- Permission ledger filter semantics (`permissionLedgerFilter.test.ts`).
- CSP presence/hardening directives in entry HTML (`csp.test.ts`).
- Replay determinism and parity contract guards (`replayDeterminism.test.ts`, `contractsParity.test.ts`).
