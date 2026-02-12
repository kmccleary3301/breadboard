# TUI Thinking + Streaming Troubleshooting and Rollback

## Common Failure Signatures

1. Status gets stuck in `compacting` or `reconnecting`.
- Check transition legality counters:
  - `state.runtimeTelemetry.illegalTransitions`
- Run strict gate tests:
  - `npm run test -- src/commands/repl/__tests__/runtimeTransitionGate.test.ts`

2. Raw reasoning appears when not expected.
- Verify guard flags are not enabled:
  - `BREADBOARD_THINKING_FULL_OPT_IN`
  - `BREADBOARD_THINKING_PEEK_RAW_ALLOWED`
- Validate safety gate:
  - `npm run test -- src/commands/repl/__tests__/runtimeSafetyGate.test.ts`

3. Streaming markdown appears jittery or reflows heavily.
- Tune coalescing:
  - `BREADBOARD_MARKDOWN_COALESCE_MS`
  - `BREADBOARD_MARKDOWN_MIN_CHUNK_CHARS`
- If testing adaptive cadence, tune:
  - `BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE`
  - `BREADBOARD_MARKDOWN_ADAPTIVE_MIN_CHUNK_CHARS`
  - `BREADBOARD_MARKDOWN_ADAPTIVE_MIN_COALESCE_MS`
  - `BREADBOARD_MARKDOWN_ADAPTIVE_BURST_CHARS`
- Evaluate jitter gate:
  - `npm run runtime:gate:jitter -- --baseline <baseline.json> --candidate <candidate.json> --strict`

4. Transcript contains too much runtime noise vs canonical conversation.
- Evaluate noise ratio gate:
  - `npm run runtime:gate:noise -- --fixture <events.jsonl> --threshold <ratio> --strict`

## Runtime Dashboard (Local)

After generating runtime gate artifacts, render a consolidated summary:

- `npm run runtime:gate:dashboard -- --artifacts ../artifacts/runtime_gates --strict-tests-ok --out ../artifacts/runtime_gates/dashboard.json --markdown-out ../artifacts/runtime_gates/dashboard.md`

This produces:
- `dashboard.json` for machine-read CI/report ingestion.
- `dashboard.md` for human triage and `GITHUB_STEP_SUMMARY`.

5. Inline thinking block appears unexpectedly.
- Verify the developer-only flag:
  - `BREADBOARD_THINKING_INLINE_COLLAPSIBLE`
- Verify provider capability overlays do not force enable:
  - `BREADBOARD_TUI_PROVIDER_CAPABILITIES_OVERRIDES`

## Rollback Controls

Use these toggles for controlled degradation without code rollback:

1. Disable activity reducer surfaces:
- `BREADBOARD_ACTIVITY_ENABLED=0`

2. Disable thinking artifact lifecycle:
- `BREADBOARD_THINKING_ENABLED=0`

3. Disable markdown coalescing path:
- `BREADBOARD_MARKDOWN_COALESCING_ENABLED=0`

4. Disable adaptive markdown cadence experiment:
- `BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE=0`

5. Force summary-only thinking behavior:
- `BREADBOARD_THINKING_FULL_OPT_IN=0`
- `BREADBOARD_THINKING_PEEK_RAW_ALLOWED=0`

6. Disable inline thinking prototype:
- `BREADBOARD_THINKING_INLINE_COLLAPSIBLE=0`

## Recovery Procedure

1. Reproduce with strict tests and capture failing summary.
2. Apply the narrowest rollback toggle needed to restore user-visible stability.
3. Re-run:
- `npm run typecheck`
- `npm run test -- src/commands/repl/__tests__/controllerActivityRuntime.test.ts src/commands/repl/__tests__/controllerActivitySequence.test.ts src/commands/repl/__tests__/runtimeTransitionGate.test.ts src/commands/repl/__tests__/runtimeSafetyGate.test.ts`
4. Generate quick triage artifacts for review:
- `npm run runtime:triage:quick -- --out-dir ../artifacts/runtime_triage`
5. Re-enable toggles incrementally and re-check gates.
