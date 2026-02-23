import { describe, expect, it } from "vitest"
import { buildTasksOverlayContract, buildTodoOverlayContract } from "../buildModalStack.js"

describe("overlay contracts", () => {
  it("buildTodoOverlayContract emits focused title + density hints for non-empty todos", () => {
    const contract = buildTodoOverlayContract([{ status: "todo" }, { status: "in_progress" }])
    expect(contract.titleLines[0]?.text).toContain("Todos")
    expect(contract.titleLines[1]?.text).toContain("FOCUS Todos")
    expect(contract.hintLines[0]?.text).toContain("2 items")
    expect(contract.hintLines[1]?.text).toContain("Status:")
  })

  it("buildTodoOverlayContract emits empty-state hint when no todos exist", () => {
    const contract = buildTodoOverlayContract([])
    expect(contract.titleLines[1]?.text).toContain("FOCUS Todos")
    expect(contract.hintLines).toHaveLength(1)
    expect(contract.hintLines[0]?.text).toContain("No todos yet")
  })

  it("buildTasksOverlayContract emits focused title + selected context for default mode", () => {
    const contract = buildTasksOverlayContract({
      tasksCount: 8,
      selectedSummary: "2/8",
      swapActive: false,
      taskFocusLaneLabel: null,
      taskFocusLaneId: "primary",
      taskFocusRawMode: false,
      taskFocusFollowTail: false,
      taskFocusTailLines: 24,
      taskSearchQuery: "",
      taskStatusFilter: "all",
      taskGroupMode: "status",
      taskLaneFilter: "all",
      statusSummary: "Status: run 1 • done 2 • failed 0 • blocked 0 • pending 5",
      taskFocusMode: "focus",
    })
    expect(contract.titleLines[0]?.text).toContain("Background tasks")
    expect(contract.titleLines[1]?.text).toContain("FOCUS Tasks")
    expect(contract.hintLines[0]?.text).toContain("selected 2/8")
    expect(contract.hintLines[2]?.text).toContain("Group: status")
  })

  it("buildTasksOverlayContract emits swap-lane hints in swap mode", () => {
    const contract = buildTasksOverlayContract({
      tasksCount: 3,
      selectedSummary: "1/3",
      swapActive: true,
      taskFocusLaneLabel: "primary",
      taskFocusLaneId: "lane-a",
      taskFocusRawMode: true,
      taskFocusFollowTail: true,
      taskFocusTailLines: 64,
      taskSearchQuery: "index",
      taskStatusFilter: "running",
      taskGroupMode: "lane",
      taskLaneFilter: "primary",
      statusSummary: "Status: run 2 • done 1 • failed 0 • blocked 0 • pending 0",
      taskFocusMode: "swap",
    })
    expect(contract.hintLines[0]?.text).toContain("lane primary")
    expect(contract.hintLines[1]?.text).toContain("view raw")
    expect(contract.hintLines[2]?.text).toContain("Lane switch")
  })
})

