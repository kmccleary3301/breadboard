# Subagents CP1 Gate Report

Date: 2026-02-13  
Scope: CP1 runtime primitives (strip/toast/no-tool-rail routing) validation

## Commands Executed

```bash
npm run subagents:capture:cp1
npm run test -- src/commands/repl/__tests__/controllerSubagentRouting.test.ts
npm run runtime:gate:subagents-scenarios
```

## Results

1. Scenario captures written:
- `tui_skeleton/docs/subagents_scenarios/cp1_async_completion_capture_20260213.json`
- `tui_skeleton/docs/subagents_scenarios/cp1_failure_sticky_toast_capture_20260213.json`
- `tui_skeleton/docs/subagents_scenarios/cp1_ascii_no_color_fallback_capture_20260213.json`

2. Routing and toast behavior tests:
- `controllerSubagentRouting.test.ts`: `9/9` tests passed

3. Strict scenario gate checks:
- CP1 captures pass acceptance checks (`failed=0`)
- CP1 captures keep `taskToolRailLinesMax=0`
- CP1 noise ratios remain within threshold (`ratio=0`)
- ASCII/NO_COLOR fallback scenario checks:
  - `asciiOnlyResolved=true`
  - `colorModeResolvedNone=true`
  - `subagentToastRendered=true`

## CP1 Gate Assessment

- `task_event` tool-rail spam in scenario captures: `0 lines`
- Completion and failure toast behavior: present and deterministic
- Sticky failure behavior: persisted at ~2s and expired by ~4.5s in capture
- Strip summary state: consistent with terminal workgraph states

Assessment: **CP1 scenario gate evidence is complete for async completion + failure sticky + ASCII/NO_COLOR fallback paths.**

Note:
- Global legacy transcript-noise matrix remains warn-only by policy; strict scoped promotion is implemented via `runtime:gate:subagents-scenarios` under CP4.
