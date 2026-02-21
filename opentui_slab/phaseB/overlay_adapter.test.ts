import { describe, expect, test } from "bun:test"
import { createOverlayAdapterState, reduceOverlayAdapterState } from "./overlay_adapter.ts"

describe("overlay adapter", () => {
  test("palette lifecycle", () => {
    let state = createOverlayAdapterState()
    state = reduceOverlayAdapterState(state, { type: "ui.palette.open", kind: "commands" })
    expect(state.activeKind).toBe("palette")
    expect(state.paletteKind).toBe("commands")
    state = reduceOverlayAdapterState(state, { type: "ui.palette.close" })
    expect(state.activeKind).toBe("none")
    expect(state.paletteKind).toBeNull()
  })

  test("permission open/close lifecycle", () => {
    let state = createOverlayAdapterState()
    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      overlayIntent: { kind: "permission", action: "open", requestId: "req-1" },
    })
    expect(state.activeKind).toBe("permission")
    expect(state.permissionRequestId).toBe("req-1")

    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      overlayIntent: { kind: "permission", action: "close", requestId: "req-1" },
    })
    expect(state.permissionRequestId).toBeNull()
  })

  test("task updates track running/completed counts", () => {
    let state = createOverlayAdapterState()
    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      normalizedEvent: {
        type: "task.update",
        taskId: "t-1",
        taskStatus: "running",
        taskDescription: "Index shard 01",
        taskLaneLabel: "lane-a",
      },
      summaryText: "running · Index shard 01",
    })
    expect(state.taskRunningCount).toBe(1)
    expect(state.taskTotalCount).toBe(1)
    expect(state.taskActiveLanes).toBe(1)
    expect(state.taskLaneCounts).toEqual({ "lane-a": 1 })
    expect(state.subagentOrder).toEqual(["lane-a"])
    expect(state.subagentById["lane-a"]?.runningCount).toBe(1)
    expect(state.activeKind).toBe("task")

    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      normalizedEvent: {
        type: "task.update",
        taskId: "t-1",
        taskStatus: "completed",
        taskLaneLabel: "lane-a",
      },
      summaryText: "completed · Index shard 01",
    })
    expect(state.taskRunningCount).toBe(0)
    expect(state.taskCompletedCount).toBe(1)
    expect(state.taskTotalCount).toBe(1)
    expect(state.subagentById["lane-a"]?.completedCount).toBe(1)
  })

  test("task overlay open/close lifecycle", () => {
    let state = createOverlayAdapterState()
    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      normalizedEvent: { type: "task.update", taskId: "t-1", taskStatus: "running" },
    })
    state = reduceOverlayAdapterState(state, { type: "ui.task.open" })
    expect(state.activeKind).toBe("task")
    state = reduceOverlayAdapterState(state, { type: "ui.task.close" })
    expect(state.activeKind).toBe("none")
  })

  test("prefers explicit child session graph ids when present", () => {
    let state = createOverlayAdapterState()
    state = reduceOverlayAdapterState(state, {
      type: "event.normalized",
      normalizedEvent: {
        type: "task.update",
        taskId: "t-child",
        taskStatus: "running",
        taskDescription: "Investigate child",
        taskSubagentId: "sess-child-42",
        taskSubagentLabel: "Child 42",
        parentSessionId: "sess-parent-1",
        childSessionId: "sess-child-42",
        taskLaneId: "lane-fallback",
        taskLaneLabel: "lane-fallback",
      },
    })
    expect(state.subagentOrder).toEqual(["sess-child-42"])
    expect(state.subagentById["sess-child-42"]).toEqual({
      id: "sess-child-42",
      label: "Child 42",
      parentSessionId: "sess-parent-1",
      childSessionId: "sess-child-42",
      taskCount: 1,
      runningCount: 1,
      completedCount: 0,
      failedCount: 0,
    })
  })
})
