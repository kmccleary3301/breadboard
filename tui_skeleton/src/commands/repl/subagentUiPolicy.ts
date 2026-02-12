import type { RuntimeBehaviorFlags } from "../../repl/types.js"

export interface SubagentUiPolicy {
  readonly v2Enabled: boolean
  readonly stripEnabled: boolean
  readonly toastsEnabled: boolean
  readonly taskboardEnabled: boolean
  readonly focusEnabled: boolean
  readonly routeTaskEventsToToolRail: boolean
  readonly routeTaskEventsToLiveSlots: boolean
  readonly toastMergeWindowMs: number
  readonly toastTtlMs: number
  readonly toastErrorTtlMs: number
}

const BOOL_TRUE = new Set(["1", "true", "yes", "on"])
const BOOL_FALSE = new Set(["0", "false", "no", "off"])

const parseBoolEnv = (value: string | undefined): boolean | null => {
  if (value == null) return null
  const normalized = value.trim().toLowerCase()
  if (!normalized) return null
  if (BOOL_TRUE.has(normalized)) return true
  if (BOOL_FALSE.has(normalized)) return false
  return null
}

const parseBoundedIntEnv = (value: string | undefined, fallback: number, min: number, max: number): number => {
  if (!value?.trim()) return fallback
  const parsed = Number.parseInt(value.trim(), 10)
  if (!Number.isFinite(parsed)) return fallback
  if (parsed < min) return min
  if (parsed > max) return max
  return parsed
}

export const resolveSubagentUiPolicy = (
  flags: RuntimeBehaviorFlags,
  env: NodeJS.ProcessEnv = process.env,
): SubagentUiPolicy => {
  const v2Enabled = flags.subagentWorkGraphEnabled
  const toolRailOverride = parseBoolEnv(env.BREADBOARD_SUBAGENTS_TASK_EVENTS_TOOL_RAIL)
  const routeTaskEventsToToolRail = toolRailOverride ?? !v2Enabled
  const toastsEnabled = flags.subagentToastsEnabled

  return {
    v2Enabled,
    stripEnabled: flags.subagentStripEnabled,
    toastsEnabled,
    taskboardEnabled: flags.subagentTaskboardEnabled,
    focusEnabled: flags.subagentFocusEnabled,
    routeTaskEventsToToolRail,
    routeTaskEventsToLiveSlots: toastsEnabled,
    toastMergeWindowMs: parseBoundedIntEnv(env.BREADBOARD_SUBAGENTS_TOAST_MERGE_MS, 750, 100, 30_000),
    toastTtlMs: parseBoundedIntEnv(env.BREADBOARD_SUBAGENTS_TOAST_TTL_MS, 1800, 250, 30_000),
    toastErrorTtlMs: parseBoundedIntEnv(env.BREADBOARD_SUBAGENTS_TOAST_ERROR_TTL_MS, 4200, 500, 60_000),
  }
}
