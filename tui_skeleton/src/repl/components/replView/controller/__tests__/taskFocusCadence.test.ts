import { describe, expect, it } from "vitest"
import {
  buildTaskFocusCadenceRequest,
  deriveTaskFocusCadencePlan,
  isTaskFocusCadenceActive,
  type TaskFocusCadenceState,
} from "../taskFocusCadence.js"

const activeState = (overrides: Partial<TaskFocusCadenceState> = {}): TaskFocusCadenceState => ({
  tasksOpen: true,
  focusViewOpen: true,
  followTail: true,
  selectedTaskId: "task-1",
  rawMode: false,
  tailLines: 24,
  refreshMs: 1200,
  ...overrides,
})

describe("taskFocusCadence", () => {
  it("reports active only when tasks/focus/follow and selected task are all present", () => {
    expect(isTaskFocusCadenceActive(activeState())).toBe(true)
    expect(isTaskFocusCadenceActive(activeState({ focusViewOpen: false }))).toBe(false)
    expect(isTaskFocusCadenceActive(activeState({ followTail: false }))).toBe(false)
    expect(isTaskFocusCadenceActive(activeState({ selectedTaskId: null }))).toBe(false)
    expect(isTaskFocusCadenceActive(activeState({ tasksOpen: false }))).toBe(false)
  })

  it("builds raw/tail request shape from state", () => {
    expect(buildTaskFocusCadenceRequest(activeState({ rawMode: true, tailLines: 80 }))).toEqual({
      raw: true,
      tailLines: 80,
    })
  })

  it("starts with immediate fetch when moving inactive -> active", () => {
    const plan = deriveTaskFocusCadencePlan(activeState({ focusViewOpen: false }), activeState())
    expect(plan).toEqual({
      active: true,
      start: true,
      stop: false,
      restart: false,
      immediate: true,
    })
  })

  it("stops when pausing follow mode", () => {
    const plan = deriveTaskFocusCadencePlan(activeState(), activeState({ followTail: false }))
    expect(plan).toEqual({
      active: false,
      start: false,
      stop: true,
      restart: false,
      immediate: false,
    })
  })

  it("restarts cadence when refresh interval changes", () => {
    const plan = deriveTaskFocusCadencePlan(activeState(), activeState({ refreshMs: 2200 }))
    expect(plan.active).toBe(true)
    expect(plan.restart).toBe(true)
    expect(plan.immediate).toBe(true)
  })

  it("triggers immediate refresh when task/raw/tail inputs change without creating a new start", () => {
    const byTask = deriveTaskFocusCadencePlan(activeState(), activeState({ selectedTaskId: "task-2" }))
    expect(byTask.active).toBe(true)
    expect(byTask.start).toBe(false)
    expect(byTask.restart).toBe(false)
    expect(byTask.immediate).toBe(true)

    const byRaw = deriveTaskFocusCadencePlan(activeState(), activeState({ rawMode: true }))
    expect(byRaw.immediate).toBe(true)
    expect(byRaw.restart).toBe(false)

    const byTail = deriveTaskFocusCadencePlan(activeState(), activeState({ tailLines: 48 }))
    expect(byTail.immediate).toBe(true)
    expect(byTail.restart).toBe(false)
  })

  it("keeps cadence stable with no-op updates (no duplicate start/restart)", () => {
    const plan = deriveTaskFocusCadencePlan(activeState(), activeState())
    expect(plan).toEqual({
      active: true,
      start: false,
      stop: false,
      restart: false,
      immediate: false,
    })
  })
})
