# Subagents Safety Audit (CP4)

Date: 2026-02-13  
Scope: Strip, toast, taskboard, and focus surfaces in `tui_skeleton`

## 1) Audit Outcome

- Result: `PASS` with one known non-blocking warning
- Blocking safety issues: `0`
- Known warning:
  - Transcript-noise warn gate remains above threshold in legacy fixtures; gate is currently warn-only and does not block release.

## 2) Surfaces and Safety Controls

1. Strip + Toast sanitization
- Code: `tui_skeleton/src/commands/repl/controllerEventMethods.ts`
- Control:
  - `sanitizeSubagentPreview` strips ANSI/control chars and truncates long text.
  - Toast dedupe/merge window avoids repeated high-frequency unsafe chatter.
- Evidence:
  - `tui_skeleton/src/commands/repl/__tests__/controllerSubagentRouting.test.ts`

2. Taskboard detail sanitization
- Code: `tui_skeleton/src/repl/components/replView/controller/taskboardStatus.ts`
- Control:
  - `sanitizeTaskPreview` strips ANSI/control chars and truncates preview strings.
  - `formatTaskStepLine` uses sanitized labels/details for checklist rendering.
- Evidence:
  - `tui_skeleton/src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts`

3. Focus default-safe behavior
- Code: `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/handleListOverlayKeys.ts`
- Control:
  - Focus opens in snippet mode (`raw=false`) by default.
  - Raw mode is explicit opt-in via `Tab` while focus modal is active.
- Evidence:
  - `tui_skeleton/src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts`

4. Focus bounded file reads
- Code: `tui_skeleton/src/repl/components/replView/controller/useReplViewPanels.tsx`
- Control:
  - Tail-first snippet reads (`headLines: 0`) for initial responsiveness.
  - Hard byte caps:
    - `BREADBOARD_SUBAGENTS_FOCUS_RAW_MAX_BYTES`
    - `BREADBOARD_SUBAGENTS_FOCUS_SNIPPET_MAX_BYTES`
  - Tail line caps and controlled load-more behavior.

5. Capability override safety hardening
- Code: `tui_skeleton/src/commands/repl/providerCapabilityResolution.ts`
- Control:
  - Provider/model whitelist validation for override schema keys.
  - Deterministic warnings for rejected keys/malformed model ids.
- Evidence:
  - `tui_skeleton/src/commands/repl/__tests__/providerCapabilityResolution.test.ts`

## 3) Gate Evidence

Run set used for this audit (2026-02-13):

```bash
npm run typecheck
npm run test -- src/repl/components/replView/controller/__tests__/focusLatency.test.ts src/repl/components/replView/controller/__tests__/subagentStrip.test.ts src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts src/commands/repl/__tests__/controllerSubagentRouting.test.ts src/commands/repl/__tests__/providerCapabilityResolution.test.ts src/tui_config/__tests__/resolveTuiConfig.test.ts
npm run runtime:gate:focus-latency
npm run runtime:gate:strip-churn
npm run runtime:gate:noise:warn
npm run runtime:validate:ascii-no-color
npm run subagents:closeout:metrics
```

Observed:
- Typecheck: pass
- Targeted tests: `75/75` pass
- Focus latency strict gate: pass
  - Open P95: `~1.5-2.0ms` (threshold `120ms`)
  - Switch P95: `~1.7-1.8ms` (threshold `90ms`)
- Strip churn strict gate: pass
- ASCII + NO_COLOR: pass
- Noise warn gate: warning only (3 fixture failures), non-blocking by current policy

## 4) Residual Risk and Mitigation

1. Residual risk: transcript noise budget remains over threshold in older fixtures
- Impact: cleanliness/UX telemetry signal, not direct safety leak
- Mitigation:
  - Keep warn-only behavior until fixture and routing parity is stabilized.
  - Revisit promotion criteria before enabling strict noise enforcement.

2. Residual risk: raw focus mode can expose more content when explicitly toggled
- Impact: operator-initiated increased visibility
- Mitigation:
  - Keep default in snippet mode.
  - Keep byte caps active and avoid default raw previews.

## 5) Safety Verdict

- CP4 safety objective for subagent surfaces is met for current rollout mode.
- Remaining warning is tracked separately as release-hygiene debt, not a blocker for this tranche.
