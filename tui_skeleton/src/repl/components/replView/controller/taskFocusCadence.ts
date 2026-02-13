export interface TaskFocusCadenceState {
  readonly tasksOpen: boolean
  readonly focusViewOpen: boolean
  readonly followTail: boolean
  readonly selectedTaskId: string | null
  readonly rawMode: boolean
  readonly tailLines: number
  readonly refreshMs: number
}

export interface TaskFocusCadencePlan {
  readonly active: boolean
  readonly start: boolean
  readonly stop: boolean
  readonly restart: boolean
  readonly immediate: boolean
}

const normalizeTaskId = (value: string | null): string | null => {
  if (typeof value !== "string") return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

export const isTaskFocusCadenceActive = (state: TaskFocusCadenceState): boolean =>
  state.tasksOpen === true &&
  state.focusViewOpen === true &&
  state.followTail === true &&
  normalizeTaskId(state.selectedTaskId) != null

export const buildTaskFocusCadenceRequest = (state: TaskFocusCadenceState): { raw: boolean; tailLines: number } => ({
  raw: state.rawMode,
  tailLines: state.tailLines,
})

export const deriveTaskFocusCadencePlan = (
  previous: TaskFocusCadenceState | null,
  next: TaskFocusCadenceState,
): TaskFocusCadencePlan => {
  const previousActive = previous ? isTaskFocusCadenceActive(previous) : false
  const nextActive = isTaskFocusCadenceActive(next)

  if (!previousActive && !nextActive) {
    return {
      active: false,
      start: false,
      stop: false,
      restart: false,
      immediate: false,
    }
  }

  if (!previousActive && nextActive) {
    return {
      active: true,
      start: true,
      stop: false,
      restart: false,
      immediate: true,
    }
  }

  if (previousActive && !nextActive) {
    return {
      active: false,
      start: false,
      stop: true,
      restart: false,
      immediate: false,
    }
  }

  const intervalChanged = (previous?.refreshMs ?? next.refreshMs) !== next.refreshMs
  const taskChanged = normalizeTaskId(previous?.selectedTaskId ?? null) !== normalizeTaskId(next.selectedTaskId)
  const modeChanged = (previous?.rawMode ?? next.rawMode) !== next.rawMode
  const tailChanged = (previous?.tailLines ?? next.tailLines) !== next.tailLines
  const immediate = intervalChanged || taskChanged || modeChanged || tailChanged

  return {
    active: true,
    start: false,
    stop: false,
    restart: intervalChanged,
    immediate,
  }
}
