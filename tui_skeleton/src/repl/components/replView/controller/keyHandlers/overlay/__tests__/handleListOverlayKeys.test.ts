import { describe, expect, it, vi } from "vitest"
import { handleListOverlayKeys } from "../handleListOverlayKeys.js"

const DEFAULT_KEY = {
  ctrl: false,
  shift: false,
  meta: false,
  upArrow: false,
  downArrow: false,
  leftArrow: false,
  rightArrow: false,
  return: false,
  escape: false,
  tab: false,
  backspace: false,
  delete: false,
  pageUp: false,
  pageDown: false,
}

const makeInfo = (overrides?: Partial<any>) => ({
  char: "",
  key: {
    ...DEFAULT_KEY,
    ...(overrides?.key ?? {}),
  },
  lowerChar: undefined,
  isReturnKey: false,
  isTabKey: false,
  isShiftTab: false,
  isCtrlT: false,
  isCtrlShiftT: false,
  isCtrlB: false,
  isCtrlY: false,
  isCtrlG: false,
  ...(overrides ?? {}),
})

const baseContext = () => ({
  todosOpen: false,
  setTodosOpen: vi.fn(),
  keymap: "codex",
  todoMaxScroll: 0,
  todoViewportRows: 10,
  setTodoScroll: vi.fn(),
  ctreeOpen: false,
  setCtreeOpen: vi.fn(),
  ctreeRows: [],
  ctreeMaxScroll: 0,
  ctreeScroll: 0,
  ctreeViewportRows: 10,
  setCtreeIndex: vi.fn(),
  setCtreeScroll: vi.fn(),
  selectedCTreeIndex: 0,
  onCtreeRefresh: vi.fn(),
  ctreeStage: "FROZEN",
  ctreeIncludePreviews: false,
  ctreeSource: "auto",
  setCtreeShowDetails: vi.fn(),
  setCtreeCollapsedNodes: vi.fn(),
  ctreeCollapsibleIds: [],
  selectedCTreeRow: null,
  tasksOpen: true,
  setTasksOpen: vi.fn(),
  taskMaxScroll: 0,
  taskScroll: 0,
  taskViewportRows: 10,
  setTaskScroll: vi.fn(),
  setTaskIndex: vi.fn(),
  taskRows: [{ id: "task-1", task: { laneId: "lane-a" } }],
  taskLaneOrder: ["lane-a", "lane-b"],
  taskFocusLaneId: null as string | null,
  taskFocusViewOpen: false,
  setTaskFocusLaneId: vi.fn(),
  setTaskFocusViewOpen: vi.fn(),
  setTaskSearchQuery: vi.fn(),
  setTaskStatusFilter: vi.fn(),
  selectedTaskIndex: 0,
  selectedTask: { laneId: "lane-a" },
  requestTaskTail: vi.fn(),
  rewindMenu: { status: "hidden", checkpoints: [] },
  rewindVisibleLimit: 5,
  rewindIndex: 0,
  setRewindIndex: vi.fn(),
  onRewindClose: vi.fn(),
  onRewindRestore: vi.fn(),
  confirmState: { status: "hidden" },
  closeConfirm: vi.fn(),
  runConfirmAction: vi.fn(),
})

describe("handleListOverlayKeys task focus mode", () => {
  it("enters focus lane mode on `f` from selected task", () => {
    const context = baseContext()
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "f", lowerChar: "f" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith("lane-a")
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(true)
    expect(context.setTaskIndex).toHaveBeenCalledWith(0)
    expect(context.setTaskScroll).toHaveBeenCalledWith(0)
  })

  it("cycles focused lane with left/right arrows inside focus view", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ key: { rightArrow: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith("lane-b")
  })

  it("cycles focused lane with bracket fallback keys", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-b" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "]", lowerChar: "]" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith("lane-a")
  })

  it("closes focus view on `f` when already focused", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "f", lowerChar: "f" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(false)
  })

  it("updates search query when typing in taskboard list mode", () => {
    const context = { ...baseContext(), taskFocusLaneId: "lane-b" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "x", lowerChar: "x" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskSearchQuery).toHaveBeenCalled()
  })

  it("suppresses search typing while focus view is open", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "x", lowerChar: "x" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskSearchQuery).not.toHaveBeenCalled()
  })
})
