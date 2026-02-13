# Subagents Strict-Promotion Runbook

Date: 2026-02-13  
Scope: Criteria and safe sequence for promoting warn-only subagent/runtime checks to strict CI blocking gates.

## Current Gate Posture

1. Strict and blocking:
- `runtime:gates:strict`
- `runtime:gate:strip-churn`
- `runtime:gate:focus-latency`
- `runtime:gate:subagents-scenarios`
- `subagents:validate:rollback`
- `subagents:bundle`

2. Warn-only:
- `runtime:gate:noise:warn` (legacy fixture set)

## Promotion Policy

1. Do not flip warn-only gates directly in CI without a stable fixture set.
2. Promote strictness only when both conditions hold:
- Condition A: dedicated fixture set is deterministic and versioned.
- Condition B: at least 3 consecutive CI runs pass without gate flake.

## Safe Promotion Steps

1. Keep legacy noise matrix as warn-only.
2. Add/maintain a strict scoped fixture gate (`runtime:gate:subagents-scenarios`) for current subagent tranche.
3. Run strict scoped gate in CI (`tui` ubuntu job) through `subagents:bundle`.
4. Track failure reasons by fixture and update fixtures before threshold changes.
5. Only after scoped strict stability is established, evaluate legacy matrix threshold tightening.

## Rollback Procedure if Strict Gate Starts Flaking

1. Immediate: switch CI invocation from strict command to warn variant for the affected scoped gate.
2. Preserve artifact emission (`summary.json`/`summary.md`) so failures remain observable.
3. Open a follow-up issue with failing fixture IDs and command output tail.
4. Re-enable strict mode only after deterministic repro and fixture fix.

## Operational Commands

```bash
# Strict scoped bundle
npm run subagents:bundle -- --out ../artifacts/subagents_bundle/summary.json --markdown-out ../artifacts/subagents_bundle/summary.md

# Scoped scenario-only gate
npm run runtime:gate:subagents-scenarios

# Rollback validation sweep
npm run subagents:validate:rollback -- --out ../artifacts/subagents_rollback/summary.json --markdown-out ../artifacts/subagents_rollback/summary.md
```
