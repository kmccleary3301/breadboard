import { describe, expect, it } from "vitest"
import {
  buildTaskGroups,
  countTaskRowsByStatusGroup,
  filterTasksForTaskboard,
  flattenTaskGroups,
  formatTaskModeBadge,
  formatTaskStepLine,
  normalizeTaskStatusGroup,
  sortTasksForLaneGrouping,
  sanitizeTaskPreview,
  sortTasksForStatusGrouping,
  taskGroupKeyForTask,
  taskStatusGroupLabel,
} from "../taskboardStatus.js"

describe("taskboardStatus", () => {
  it("normalizes variant status strings into canonical groups", () => {
    expect(normalizeTaskStatusGroup("in_progress")).toBe("running")
    expect(normalizeTaskStatusGroup("waiting_for_output")).toBe("blocked")
    expect(normalizeTaskStatusGroup("ERROR")).toBe("failed")
    expect(normalizeTaskStatusGroup("done")).toBe("completed")
    expect(normalizeTaskStatusGroup("stopped")).toBe("cancelled")
    expect(normalizeTaskStatusGroup(undefined)).toBe("pending")
  })

  it("sorts tasks by status group priority then recency", () => {
    const sorted = sortTasksForStatusGrouping([
      { id: "completed-old", status: "completed", updatedAt: 1 },
      { id: "running-old", status: "running", updatedAt: 1 },
      { id: "failed-new", status: "failed", updatedAt: 3 },
      { id: "running-new", status: "running", updatedAt: 5 },
      { id: "blocked-mid", status: "blocked", updatedAt: 2 },
      { id: "pending-new", status: "pending", updatedAt: 9 },
    ])
    expect(sorted.map((task) => task.id)).toEqual([
      "running-new",
      "running-old",
      "blocked-mid",
      "failed-new",
      "completed-old",
      "pending-new",
    ])
  })

  it("counts rows per canonical status group", () => {
    const counts = countTaskRowsByStatusGroup([
      { status: "running" },
      { status: "in_progress" },
      { status: "completed" },
      { status: "error" },
      { status: "blocked" },
      { status: "cancelled" },
      { status: "unknown" },
    ])
    expect(counts).toEqual({
      running: 2,
      blocked: 1,
      failed: 1,
      completed: 1,
      cancelled: 1,
      pending: 1,
    })
  })

  it("provides human labels for group headers", () => {
    expect(taskStatusGroupLabel("running")).toBe("Running")
    expect(taskStatusGroupLabel("blocked")).toBe("Blocked")
    expect(taskStatusGroupLabel("failed")).toBe("Failed")
    expect(taskStatusGroupLabel("completed")).toBe("Completed")
    expect(taskStatusGroupLabel("cancelled")).toBe("Cancelled")
    expect(taskStatusGroupLabel("pending")).toBe("Pending")
  })

  it("builds stable group keys by status and lane", () => {
    expect(taskGroupKeyForTask({ status: "running", laneId: "lane-a" }, "status")).toBe("status:running")
    expect(taskGroupKeyForTask({ status: "running", laneId: "lane-a" }, "lane")).toBe("lane:lane-a")
    expect(taskGroupKeyForTask({ status: "", laneId: "" }, "lane")).toBe("lane:primary")
  })

  it("sorts lane grouping by lane order then status then recency", () => {
    const sorted = sortTasksForLaneGrouping(
      [
        { id: "lane-b-running", laneId: "lane-b", status: "running", updatedAt: 2 },
        { id: "lane-a-completed", laneId: "lane-a", status: "completed", updatedAt: 5 },
        { id: "lane-c-failed", laneId: "lane-c", status: "failed", updatedAt: 8 },
        { id: "lane-a-running", laneId: "lane-a", status: "running", updatedAt: 1 },
      ],
      ["lane-a", "lane-b"],
    )
    expect(sorted.map((task) => task.id)).toEqual([
      "lane-a-running",
      "lane-a-completed",
      "lane-b-running",
      "lane-c-failed",
    ])
  })

  it("builds grouped collections for status and lane modes", () => {
    const tasks = [
      { id: "a", laneId: "lane-a", status: "running", updatedAt: 5 },
      { id: "b", laneId: "lane-b", status: "failed", updatedAt: 4 },
      { id: "c", laneId: "lane-a", status: "completed", updatedAt: 2 },
    ]
    const byStatus = buildTaskGroups(tasks, { mode: "status" })
    expect(byStatus.map((group) => group.key)).toEqual(["status:running", "status:failed", "status:completed"])

    const byLane = buildTaskGroups(tasks, {
      mode: "lane",
      laneOrder: ["lane-b", "lane-a"],
      laneLabelById: { "lane-a": "Primary lane", "lane-b": "Worker lane" },
    })
    expect(byLane.map((group) => group.key)).toEqual(["lane:lane-b", "lane:lane-a"])
    expect(byLane[0]?.label).toBe("Worker lane")
    expect(byLane[1]?.label).toBe("Primary lane")
  })

  it("preserves collapsed-group filtering when task updates arrive", () => {
    const initial = buildTaskGroups(
      [
        { id: "a", laneId: "lane-a", status: "running", updatedAt: 1 },
        { id: "b", laneId: "lane-b", status: "completed", updatedAt: 1 },
      ],
      { mode: "status" },
    )
    const collapsed = new Set<string>(["status:running"])
    const initialRows = flattenTaskGroups(initial, collapsed)
    expect(initialRows.map((entry) => entry.task.id)).toEqual(["b"])

    const updated = buildTaskGroups(
      [
        { id: "a", laneId: "lane-a", status: "running", updatedAt: 2 },
        { id: "c", laneId: "lane-c", status: "running", updatedAt: 3 },
        { id: "b", laneId: "lane-b", status: "completed", updatedAt: 2 },
      ],
      { mode: "status" },
    )
    const updatedRows = flattenTaskGroups(updated, collapsed)
    expect(updatedRows.map((entry) => entry.task.id)).toEqual(["b"])
  })

  it("composes query, status, and lane filters deterministically", () => {
    const tasks = [
      { id: "a", laneId: "lane-a", status: "running", description: "index repo" },
      { id: "b", laneId: "lane-a", status: "failed", description: "index retry" },
      { id: "c", laneId: "lane-b", status: "running", description: "test suite" },
    ]

    const laneFiltered = filterTasksForTaskboard(tasks, { laneFilter: "lane-a" })
    expect(laneFiltered.map((task) => task.id)).toEqual(["a", "b"])

    const statusAndLaneFiltered = filterTasksForTaskboard(tasks, {
      laneFilter: "lane-a",
      statusFilter: "running",
    })
    expect(statusAndLaneFiltered.map((task) => task.id)).toEqual(["a"])

    const queryStatusLaneFiltered = filterTasksForTaskboard(tasks, {
      laneFilter: "lane-a",
      statusFilter: "failed",
      query: "retry",
    })
    expect(queryStatusLaneFiltered.map((task) => task.id)).toEqual(["b"])
  })

  it("sanitizes preview text for taskboard detail surfaces", () => {
    const input = `\u001b[31msecret\u001b[0m \u0007 value ${"x".repeat(260)}`
    const preview = sanitizeTaskPreview(input, 120)
    expect(preview).toBeTruthy()
    expect(preview).not.toContain("\u001b")
    expect(preview).not.toContain("\u0007")
    expect(preview?.endsWith("...")).toBe(true)
  })

  it("formats mode badges for foreground/background tasks", () => {
    expect(formatTaskModeBadge("sync")).toBe("[fg]")
    expect(formatTaskModeBadge("foreground")).toBe("[fg]")
    expect(formatTaskModeBadge("async")).toBe("[bg]")
    expect(formatTaskModeBadge("background")).toBe("[bg]")
    expect(formatTaskModeBadge("unknown")).toBeNull()
  })

  it("formats step lines with retry and terminal reason semantics", () => {
    const retryLine = formatTaskStepLine({
      label: "call tool",
      status: "running",
      attempt: 3,
      detail: "still executing",
    })
    expect(retryLine).toContain("[running]")
    expect(retryLine).toContain("(attempt 3)")

    const timeoutLine = formatTaskStepLine({
      label: "fetch output",
      status: "failed",
      detail: "timeout after 5s",
    })
    expect(timeoutLine).toContain("[failed/timeout]")

    const cancelledLine = formatTaskStepLine({
      label: "worker cancelled by operator",
      status: "cancelled",
      detail: "cancel requested",
    })
    expect(cancelledLine).toContain("[cancelled/cancelled]")
  })
})
