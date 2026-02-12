type RuntimeValidationMode = "warn" | "strict"

const TERMINAL_ACTIVITY = new Set(["completed", "halted", "cancelled", "error"])

export interface RuntimeScenarioValidationResult {
  readonly mode: RuntimeValidationMode
  readonly ok: boolean
  readonly warnings: ReadonlyArray<string>
  readonly summary: string
}

export const collectRuntimeWarnings = (state: any): string[] => {
  const warnings: string[] = []
  if ((state.runtimeTelemetry?.illegalTransitions ?? 0) > 0) {
    warnings.push(`illegal-transitions:${state.runtimeTelemetry.illegalTransitions}`)
  }
  if (state.completionSeen && !TERMINAL_ACTIVITY.has(state.activity?.primary)) {
    warnings.push(`completion-with-nonterminal-activity:${state.activity?.primary ?? "none"}`)
  }
  if (state.permissionRequest == null && state.activity?.primary === "permission_required") {
    warnings.push("permission-required-without-active-request")
  }
  return warnings
}

export const validateRuntimeScenario = (
  state: any,
  mode: RuntimeValidationMode = "warn",
): RuntimeScenarioValidationResult => {
  const warnings = collectRuntimeWarnings(state)
  const ok = mode === "warn" || warnings.length === 0
  const summary =
    warnings.length === 0
      ? "ok"
      : warnings
          .slice()
          .sort((a, b) => a.localeCompare(b))
          .join("|")
  return { mode, ok, warnings, summary }
}
