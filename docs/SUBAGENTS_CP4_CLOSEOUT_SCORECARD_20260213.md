# Subagents CP4 Closeout Scorecard

Date: 2026-02-13  
Branch: `feature/subagents-v2-cp0`  
Primary commits:
- `d8139728` (`tui: harden subagent taskboard, focus controls, and runtime gates`)
- current tranche (focus latency gate + safety/closeout docs)

## 1) Scope Closed in This Tranche

Issue: `ray_SCE-e8s`  
Requested closeout items:

1. Add focus open/switch latency gate with deterministic harness.
2. Complete cross-surface safety audit.
3. Publish final CP4 closeout scorecard and evidence index.

Status: `DONE`

## 2) Delivered Artifacts

1. Focus latency gate + thresholds
- `tui_skeleton/scripts/runtime_focus_latency_gate.ts`
- `tui_skeleton/src/repl/components/replView/controller/focusLatency.ts`
- `tui_skeleton/src/repl/components/replView/controller/__tests__/focusLatency.test.ts`
- `tui_skeleton/config/runtime_gate_thresholds.json`
- `tui_skeleton/package.json` scripts:
  - `runtime:gate:focus-latency`
  - `runtime:gate:focus-latency:warn`

2. Closeout metric pipeline updated
- `tui_skeleton/scripts/subagents_closeout_metrics.ts`
  - includes `warn-focus-latency-gate`

3. Strict scoped subagent gates + bundle
- `tui_skeleton/scripts/subagents_scenario_gate.ts`
- `tui_skeleton/scripts/subagents_runtime_bundle.ts`
- `tui_skeleton/scripts/subagents_rollback_validation.ts`
- `artifacts/subagents_bundle/summary.json`
- `artifacts/subagents_rollback/summary.json`
- CI wiring: `.github/workflows/ci.yml` (`Subagents runtime bundle` step)

4. Safety audit document
- `docs/SUBAGENTS_SAFETY_AUDIT_CP4_20260213.md`

5. Compatibility + rollback + weighted closeout docs
- `docs/SUBAGENTS_COMPATIBILITY_AUDIT_20260213.md`
- `docs/SUBAGENTS_ROLLBACK_VALIDATION_20260213.md`
- `docs/SUBAGENTS_STRICT_PROMOTION_RUNBOOK_20260213.md`
- `docs/SUBAGENTS_FINAL_WEIGHTED_CLOSEOUT_REPORT_20260213.md`

## 3) Evidence Index (Command + Result)

Execution timestamp set: 2026-02-13

1. Static checks
- Command: `npm run typecheck`
- Result: pass

2. Targeted regression suite
- Command:
```bash
npm run test -- src/repl/components/replView/controller/__tests__/focusLatency.test.ts src/repl/components/replView/controller/__tests__/subagentStrip.test.ts src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts src/commands/repl/__tests__/controllerSubagentRouting.test.ts src/commands/repl/__tests__/providerCapabilityResolution.test.ts src/tui_config/__tests__/resolveTuiConfig.test.ts
```
- Result: `8 files, 75 tests` pass

3. Focus latency strict gate
- Command: `npm run runtime:gate:focus-latency`
- Result: pass
- Observed:
  - `openP95Ms` approx `1.5-2.0ms` (threshold `120ms`)
  - `switchP95Ms` approx `1.7-1.8ms` (threshold `90ms`)

4. Strip churn strict gate
- Command: `npm run runtime:gate:strip-churn`
- Result: pass

5. Strict scoped scenario gate
- Command: `npm run runtime:gate:subagents-scenarios`
- Result: pass (`ok: true`, `failed: 0`)

6. Deterministic subagents bundle
- Command: `npm run subagents:bundle -- --out ../artifacts/subagents_bundle/summary.json --markdown-out ../artifacts/subagents_bundle/summary.md`
- Result: pass (`ok: true`, `failed: 0`, `total: 10`)

7. Rollback level validation
- Command: `npm run subagents:validate:rollback -- --out ../artifacts/subagents_rollback/summary.json --markdown-out ../artifacts/subagents_rollback/summary.md`
- Result: pass (`ok: true`, `failedLevels: 0`)

8. ASCII/NO_COLOR
- Command: `npm run runtime:validate:ascii-no-color`
- Result: pass

9. Consolidated closeout metrics
- Command: `npm run subagents:closeout:metrics`
- Result: pass (`ok: true`, `failed: 0`)

## 4) CP4 Scorecard

1. Runtime hardening gates present:
- noise warn gate: yes
- strip churn gate: yes
- focus latency gate: yes
- strict scoped subagents scenario gate: yes
- strict deterministic subagents bundle gate: yes

2. Safety audit published:
- yes (`docs/SUBAGENTS_SAFETY_AUDIT_CP4_20260213.md`)

3. Rollback/knobs documentation:
- yes (`docs/SUBAGENTS_ROLLBACK_VALIDATION_20260213.md`)

4. Residual non-blocking debt:
- transcript noise fixtures still above warn threshold
- tracked for later strict promotion decision
- optional CP3 cache optimization and expansion track remain open

## 5) Release Readiness for This Tranche

- Functional tranche status: ready
- Safety status: pass with documented residual warning
- CI/gate posture: strict scoped subagent bundle enforced in CI (ubuntu TUI job)
