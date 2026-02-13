# Subagents V2 Completion Tracker

Date: 2026-02-13  
Scope baseline: `docs_tmp/cli_phase_4/SUBAGENTS_PRIMITIVES_AND_DISPLAY_EXECUTION_PLAN_V2.md`  
Snapshot branch: `feature/subagents-v2-cp0`  
Snapshot commit floor: `05455d24`

Status legend:
- `done`: implemented and evidenced in code/tests/docs
- `partial`: implemented in part, missing required capture/gate/docs acceptance criterion
- `todo`: not implemented yet

## CP0 Status

| ID | Status | Evidence |
| --- | --- | --- |
| CP0-001 | done | `tui_skeleton/src/repl/types.ts` |
| CP0-002 | done | `tui_skeleton/src/repl/types.ts` |
| CP0-003 | done | `tui_skeleton/src/commands/repl/controllerWorkGraphRuntime.ts` |
| CP0-004 | done | `tui_skeleton/src/commands/repl/controllerWorkGraphRuntime.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP0-005 | done | `tui_skeleton/src/commands/repl/controllerWorkGraphRuntime.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP0-006 | done | `tui_skeleton/src/commands/repl/controller.ts`, `tui_skeleton/src/commands/repl/controllerActivityRuntime.ts` |
| CP0-007 | done | `tui_skeleton/src/commands/repl/controller.ts`, `tui_skeleton/src/repl/types.ts` |
| CP0-008 | done | `tui_skeleton/src/commands/repl/controllerActivityRuntime.ts` |
| CP0-009 | done | `tui_skeleton/src/commands/repl/subagentUiPolicy.ts`, `tui_skeleton/src/commands/repl/__tests__/subagentUiPolicy.test.ts` |
| CP0-010 | done | `tui_skeleton/src/tui_config/types.ts`, `tui_skeleton/src/tui_config/presets.ts`, `tui_skeleton/src/tui_config/__tests__/resolveTuiConfig.test.ts` |
| CP0-011 | done | `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP0-012 | done | `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP0-013 | done | `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP0-014 | partial | Flags-off behavior covered in runtime flag tests; no dedicated snapshot evidence bundle linked |
| CP0-015 | done | `docs_tmp/cli_phase_4/SUBAGENTS_CONTRACT_HANDSHAKE_20260213.md` |
| CP0-016 | done | `tui_skeleton/src/commands/repl/controllerWorkGraphRuntime.ts`, `tui_skeleton/src/commands/repl/controller.ts` |
| CP0-017 | done | `docs_tmp/cli_phase_4/SUBAGENTS_WORKGRAPH_FLOW_DIAGRAM.md`, linked from handshake doc |

## CP1 Status

| ID | Status | Evidence |
| --- | --- | --- |
| CP1-001 | done | `tui_skeleton/src/repl/components/replView/controller/subagentStrip.ts`, `tui_skeleton/src/repl/components/replView/controller/useReplViewScrollback.tsx` |
| CP1-002 | done | `tui_skeleton/src/repl/components/replView/controller/subagentStrip.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/subagentStrip.test.ts` |
| CP1-003 | done | `tui_skeleton/src/repl/components/replView/controller/subagentStrip.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/subagentStrip.test.ts` |
| CP1-004 | done | `tui_skeleton/src/repl/components/replView/controller/subagentStrip.ts`, `tui_skeleton/src/repl/components/replView/controller/useReplViewScrollback.tsx` |
| CP1-005 | done | `tui_skeleton/src/commands/repl/controllerEventMethods.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerSubagentRouting.test.ts` |
| CP1-006 | done | `tui_skeleton/src/commands/repl/controllerEventMethods.ts`, `tui_skeleton/src/commands/repl/subagentUiPolicy.ts` |
| CP1-007 | done | `tui_skeleton/scripts/subagents_scenario_gate.ts`, `npm run runtime:gate:subagents-scenarios` strict pass |
| CP1-008 | done | `tui_skeleton/src/commands/repl/controllerEventMethods.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerSubagentRouting.test.ts` |
| CP1-009 | done | `tui_skeleton/src/commands/repl/controllerEventMethods.ts` |
| CP1-010 | done | `docs/SUBAGENTS_SAFETY_AUDIT_CP4_20260213.md` |
| CP1-011 | done | `tui_skeleton/src/repl/components/replView/controller/__tests__/subagentStrip.test.ts` |
| CP1-012 | done | `tui_skeleton/src/commands/repl/__tests__/controllerSubagentRouting.test.ts` |
| CP1-013 | done | `tui_skeleton/docs/subagents_scenarios/cp1_async_completion_capture_20260213.json` |
| CP1-014 | done | `tui_skeleton/docs/subagents_scenarios/cp1_failure_sticky_toast_capture_20260213.json` |
| CP1-015 | done | `tui_skeleton/docs/subagents_scenarios/cp1_ascii_no_color_fallback_capture_20260213.json`, `docs/SUBAGENTS_CP1_GATE_REPORT_20260213.md` |

## CP2 Status

| ID | Status | Evidence |
| --- | --- | --- |
| CP2-001 | done | `tui_skeleton/src/repl/components/replView/controller/useReplViewPanels.tsx` |
| CP2-002 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts` |
| CP2-003 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts` |
| CP2-004 | done | `tui_skeleton/src/repl/components/replView/controller/useReplViewPanels.tsx`, `tui_skeleton/src/repl/components/replView/overlays/buildModalStack.tsx` |
| CP2-005 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/handleListOverlayKeys.ts` |
| CP2-006 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `tui_skeleton/src/repl/components/replView/overlays/buildModalStack.tsx` |
| CP2-007 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP2-008 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts` |
| CP2-009 | done | `tui_skeleton/src/commands/repl/controllerWorkGraphRuntime.ts`, `tui_skeleton/src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts` |
| CP2-010 | done | `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`, `docs/SUBAGENTS_SAFETY_AUDIT_CP4_20260213.md` |
| CP2-011 | done | `tui_skeleton/src/repl/components/replView/controller/useReplViewController.tsx` |
| CP2-012 | done | `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts` |
| CP2-013 | done | `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts` |
| CP2-014 | done | `tui_skeleton/docs/subagents_scenarios/cp2_sync_progression_capture_20260213.json` |
| CP2-015 | done | `tui_skeleton/docs/subagents_scenarios/cp2_async_wakeup_output_ready_capture_20260213.json` |
| CP2-016 | done | `tui_skeleton/docs/subagents_scenarios/cp2_failure_retry_completion_capture_20260213.json` |
| CP2-017 | done | `tui_skeleton/docs/subagents_scenarios/cp2_concurrency_20_tasks_capture_20260213.json` |
| CP2-018 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/handleListOverlayKeys.ts`, tests in `handleListOverlayKeys.test.ts` |

## CP3 Status

| ID | Status | Evidence |
| --- | --- | --- |
| CP3-001 | done | `tui_skeleton/src/repl/components/replView/overlays/buildModalStack.tsx`, overlay handler wiring |
| CP3-002 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/handleListOverlayKeys.ts`, tests |
| CP3-003 | done | `tui_skeleton/src/repl/components/replView/overlays/buildModalStack.tsx` |
| CP3-004 | done | `tui_skeleton/src/repl/components/replView/controller/useReplViewPanels.tsx` (tail-first snippet reads) |
| CP3-005 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/handleListOverlayKeys.ts` |
| CP3-006 | partial | Follow/pause behavior implemented; explicit cadence-focused unit assertions still limited |
| CP3-007 | done | `tui_skeleton/src/repl/components/replView/controller/useReplViewPanels.tsx` |
| CP3-008 | done | `tui_skeleton/src/repl/components/replView/controller/taskFocusLoader.ts`, `tui_skeleton/src/repl/components/replView/controller/__tests__/taskFocusLoader.test.ts` |
| CP3-009 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts` |
| CP3-010 | done | Default snippet + explicit raw toggle behavior documented and tested |
| CP3-011 | done | `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts` |
| CP3-012 | partial | Key toggle tests present; cadence timing tests pending |
| CP3-013 | done | `tui_skeleton/docs/subagents_scenarios/cp3_focus_active_updates_capture_20260213.json`, `docs/SUBAGENTS_CP3_GATE_REPORT_20260213.md` |
| CP3-014 | done | `tui_skeleton/docs/subagents_scenarios/cp3_focus_rapid_lane_switch_capture_20260213.json`, `docs/SUBAGENTS_CP3_GATE_REPORT_20260213.md` |
| CP3-015 | done | `tui_skeleton/scripts/runtime_focus_latency_gate.ts`, `focusLatency.test.ts` |
| CP3-016 | todo | Optional cache optimization not yet implemented |

## CP4 Status

| ID | Status | Evidence |
| --- | --- | --- |
| CP4-001 | done | `tui_skeleton/src/tui_config/presets.ts`, `resolveTuiConfig.test.ts` |
| CP4-002 | done | `tui_skeleton/src/tui_config/presets.ts`, `resolveTuiConfig.test.ts` |
| CP4-003 | done | `tui_skeleton/src/commands/repl/providerCapabilityResolution.ts`, tests |
| CP4-004 | done | `tui_skeleton/scripts/subagents_scenario_gate.ts`, strict pass via `runtime:gate:subagents-scenarios` |
| CP4-005 | done | `tui_skeleton/scripts/runtime_strip_churn_gate.ts` |
| CP4-006 | done | `tui_skeleton/scripts/runtime_focus_latency_gate.ts` |
| CP4-007 | done | `tui_skeleton/scripts/subagents_runtime_bundle.ts`, `artifacts/subagents_bundle/summary.json` |
| CP4-008 | done | `.github/workflows/ci.yml` (`Subagents runtime bundle` step + artifact upload) |
| CP4-009 | done | `docs/SUBAGENTS_SAFETY_AUDIT_CP4_20260213.md` |
| CP4-010 | done | `docs/SUBAGENTS_COMPATIBILITY_AUDIT_20260213.md` |
| CP4-011 | done | `tui_skeleton/scripts/subagents_rollback_validation.ts`, `docs/SUBAGENTS_ROLLBACK_VALIDATION_20260213.md` |
| CP4-012 | done | `docs/SUBAGENTS_FINAL_WEIGHTED_CLOSEOUT_REPORT_20260213.md`, `artifacts/subagents_bundle/summary.json` |
| CP4-013 | done | `docs/SUBAGENTS_STRICT_PROMOTION_RUNBOOK_20260213.md` |

## Expansion Track Status

| ID | Status | Evidence |
| --- | --- | --- |
| EX1-001 | todo | No `focus.mode=swap` implementation yet |
| EX1-002 | todo | No swap-mode stress scenarios yet |
| EX2-001 | todo | No lane offset index cache optimization yet |
| EX3-001 | todo | No hotspot diagnostics heatmap yet |
| EX4-001 | todo | Additional preset variants beyond baseline pair not yet added |

## Gate Summary Snapshot

- `Gate A (MVP Go)`: done  
- `Gate B (Operator Go)`: partial  
- `Gate C (Full Tranche Go)`: partial

Blocking deltas to full tranche go:
1. CP0-014 remains partial (no dedicated flags-off snapshot bundle).
2. CP3-006 and CP3-012 remain partial (cadence-focused assertions can be expanded).
3. CP3-016 optional cache optimization is not implemented.
4. Expansion track (`EX1`..`EX4`) has not started.
