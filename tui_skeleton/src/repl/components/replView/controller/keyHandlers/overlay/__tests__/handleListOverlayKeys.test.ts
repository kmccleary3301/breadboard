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
  home: false,
  end: false,
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
  isHomeKey: false,
  isEndKey: false,
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
  taskFocusFollowTail: true,
  taskFocusRawMode: false,
  taskFocusTailLines: 24,
  taskFocusDefaultTailLines: 24,
  taskFocusMode: "lane" as "lane" | "swap",
  taskLaneFilter: "all",
  taskGroupMode: "status" as "status" | "lane",
  taskCollapsedGroupKeys: new Set<string>(),
  setTaskFocusLaneId: vi.fn(),
  setTaskFocusViewOpen: vi.fn(),
  setTaskFocusFollowTail: vi.fn(),
  setTaskFocusRawMode: vi.fn(),
  setTaskFocusTailLines: vi.fn(),
  setTaskLaneFilter: vi.fn(),
  setTaskGroupMode: vi.fn(),
  setTaskCollapsedGroupKeys: vi.fn(),
  setTaskSearchQuery: vi.fn(),
  setTaskStatusFilter: vi.fn(),
  taskboardInputQuarantineUntilRef: { current: 0 },
  setTaskNotice: vi.fn(),
  setTaskActionNotice: vi.fn(),
  selectedTaskIndex: 0,
  selectedTaskRow: { groupKey: "status:running", groupLabel: "Running", task: { laneId: "lane-a" } },
  selectedTask: { id: "task-1", laneId: "lane-a" },
  requestTaskTail: vi.fn(),
  exportTaskLog: vi.fn(),
  rewindMenu: { status: "hidden", checkpoints: [] },
  rewindVisibleLimit: 5,
  rewindIndex: 0,
  setRewindIndex: vi.fn(),
  onRewindClose: vi.fn(),
  onRewindRestore: vi.fn(),
  confirmState: { status: "hidden" },
  closeConfirm: vi.fn(),
  runConfirmAction: vi.fn(),
  jumpTranscriptToLine: vi.fn(),
})

describe("handleListOverlayKeys task focus mode", () => {
  it("refreshes and attaches from the recent-sessions overlay", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      recentSessionsOpen: true,
      recentSessionsRows: [
        { sessionId: "sess-a", status: "ready" },
        { sessionId: "sess-b", status: "finished" },
      ],
      recentSessionsIndex: 1,
      recentSessionsScroll: 0,
      recentSessionsMaxScroll: 1,
      recentSessionsViewportRows: 8,
      setRecentSessionsOpen: vi.fn(),
      setRecentSessionsIndex: vi.fn(),
      setRecentSessionsScroll: vi.fn(),
      refreshRecentSessions: vi.fn(),
      attachRecentSession: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ char: "r", lowerChar: "r" }))).toBe(true)
    expect(context.refreshRecentSessions).toHaveBeenCalled()
    expect(handleListOverlayKeys(context, makeInfo({ isReturnKey: true, key: { return: true } }))).toBe(true)
    expect(context.attachRecentSession).toHaveBeenCalledWith("sess-b")
  })

  it("does not attach on the same return keypress that opened recent sessions", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      recentSessionsOpen: true,
      recentSessionsOpenedAtRef: { current: Date.now() },
      recentSessionsRows: [{ sessionId: "sess-a", status: "ready" }],
      recentSessionsIndex: 0,
      recentSessionsScroll: 0,
      recentSessionsMaxScroll: 0,
      recentSessionsViewportRows: 8,
      setRecentSessionsOpen: vi.fn(),
      setRecentSessionsIndex: vi.fn(),
      setRecentSessionsScroll: vi.fn(),
      refreshRecentSessions: vi.fn(),
      attachRecentSession: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ isReturnKey: true, key: { return: true } }))).toBe(true)
    expect(context.attachRecentSession).not.toHaveBeenCalled()
  })

  it("closes the recent-sessions overlay on escape", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      recentSessionsOpen: true,
      recentSessionsRows: [{ sessionId: "sess-a", status: "ready" }],
      recentSessionsIndex: 0,
      recentSessionsScroll: 0,
      recentSessionsMaxScroll: 0,
      recentSessionsViewportRows: 8,
      setRecentSessionsOpen: vi.fn(),
      setRecentSessionsIndex: vi.fn(),
      setRecentSessionsScroll: vi.fn(),
      refreshRecentSessions: vi.fn(),
      attachRecentSession: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ char: "\u001b", key: { escape: true } }))).toBe(true)
    expect(context.setRecentSessionsOpen).toHaveBeenCalledWith(false)
  })

  it("scrolls and closes the collapsed-detail overlay", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      collapsedDetailOpen: true,
      collapsedDetailScroll: 3,
      collapsedDetailMaxScroll: 12,
      collapsedDetailViewportRows: 5,
      setCollapsedDetailOpen: vi.fn(),
      setCollapsedDetailScroll: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ key: { pageDown: true } }))).toBe(true)
    expect(context.setCollapsedDetailScroll).toHaveBeenCalled()
    expect(handleListOverlayKeys(context, makeInfo({ char: "\u001b", key: { escape: true } }))).toBe(true)
    expect(context.setCollapsedDetailOpen).toHaveBeenCalledWith(false)
  })

  it("jumps back to transcript source from result detail", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      resultDetailOpen: true,
      resultDetailScroll: 2,
      resultDetailMaxScroll: 12,
      resultDetailViewportRows: 5,
      resultDetailArtifactPath: "artifacts/report.txt",
      resultDetailSourceLine: 14,
      setResultDetailOpen: vi.fn(),
      setResultDetailScroll: vi.fn(),
      openSelectedTranscriptArtifactPreview: vi.fn(() => true),
      artifactPreviewOpen: false,
      artifactPreviewScroll: 0,
      artifactPreviewMaxScroll: 0,
      artifactPreviewViewportRows: 5,
      artifactPreviewSourceLine: 14,
      setArtifactPreviewOpen: vi.fn(),
      setArtifactPreviewScroll: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ char: "j", lowerChar: "j" }))).toBe(true)
    expect(context.setResultDetailOpen).toHaveBeenCalledWith(false)
    expect(context.jumpTranscriptToLine).toHaveBeenCalledWith(14)
  })

  it("jumps back to transcript source from artifact preview", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      resultDetailOpen: true,
      resultDetailScroll: 2,
      resultDetailMaxScroll: 12,
      resultDetailViewportRows: 5,
      resultDetailArtifactPath: "artifacts/report.txt",
      resultDetailSourceLine: 14,
      setResultDetailOpen: vi.fn(),
      setResultDetailScroll: vi.fn(),
      openSelectedTranscriptArtifactPreview: vi.fn(() => true),
      artifactPreviewOpen: true,
      artifactPreviewScroll: 3,
      artifactPreviewMaxScroll: 12,
      artifactPreviewViewportRows: 5,
      artifactPreviewSourceLine: 14,
      setArtifactPreviewOpen: vi.fn(),
      setArtifactPreviewScroll: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ char: "j", lowerChar: "j" }))).toBe(true)
    expect(context.setArtifactPreviewOpen).toHaveBeenCalledWith(false)
    expect(context.setResultDetailOpen).toHaveBeenCalledWith(false)
    expect(context.jumpTranscriptToLine).toHaveBeenCalledWith(14)
  })

  it("opens artifact preview from result detail and closes both overlays with escape", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      resultDetailOpen: true,
      resultDetailScroll: 2,
      resultDetailMaxScroll: 12,
      resultDetailViewportRows: 5,
      resultDetailArtifactPath: "artifacts/report.txt",
      resultDetailSourceLine: 14,
      setResultDetailOpen: vi.fn(),
      setResultDetailScroll: vi.fn(),
      openSelectedTranscriptArtifactPreview: vi.fn(() => true),
      artifactPreviewOpen: false,
      artifactPreviewScroll: 0,
      artifactPreviewMaxScroll: 0,
      artifactPreviewViewportRows: 5,
      artifactPreviewSourceLine: 14,
      setArtifactPreviewOpen: vi.fn(),
      setArtifactPreviewScroll: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ isReturnKey: true, key: { return: true } }))).toBe(true)
    expect(context.openSelectedTranscriptArtifactPreview).toHaveBeenCalled()
    expect(handleListOverlayKeys(context, makeInfo({ char: "\u001b", key: { escape: true } }))).toBe(true)
    expect(context.setResultDetailOpen).toHaveBeenCalledWith(false)
  })

  it("scrolls and closes artifact preview overlay", () => {
    const context = {
      ...baseContext(),
      tasksOpen: false,
      resultDetailOpen: false,
      artifactPreviewOpen: true,
      artifactPreviewScroll: 3,
      artifactPreviewMaxScroll: 12,
      artifactPreviewViewportRows: 5,
      setArtifactPreviewOpen: vi.fn(),
      setArtifactPreviewScroll: vi.fn(),
    }
    expect(handleListOverlayKeys(context, makeInfo({ key: { pageDown: true } }))).toBe(true)
    expect(context.setArtifactPreviewScroll).toHaveBeenCalled()
    expect(handleListOverlayKeys(context, makeInfo({ char: "\u001b", key: { escape: true } }))).toBe(true)
    expect(context.setArtifactPreviewOpen).toHaveBeenCalledWith(false)
  })

  it("closes todos overlay from raw Ctrl+T control char in claude keymap", () => {
    const context = { ...baseContext(), todosOpen: true, tasksOpen: false, keymap: "claude" }
    const handled = handleListOverlayKeys(context, makeInfo({ char: "\u0014" }))
    expect(handled).toBe(true)
    expect(context.setTodosOpen).toHaveBeenCalledWith(false)
  })

  it("closes tasks overlay from raw Ctrl+B control char", () => {
    const context = { ...baseContext(), todosOpen: false, tasksOpen: true }
    const handled = handleListOverlayKeys(context, makeInfo({ char: "\u0002" }))
    expect(handled).toBe(true)
    expect(context.setTasksOpen).toHaveBeenCalledWith(false)
  })

  it("closes todos overlay when ctrl key uses key.name without char payload", () => {
    const context = { ...baseContext(), todosOpen: true, tasksOpen: false, keymap: "claude" }
    const handled = handleListOverlayKeys(context, makeInfo({ key: { ctrl: true, name: "t" } }))
    expect(handled).toBe(true)
    expect(context.setTodosOpen).toHaveBeenCalledWith(false)
  })

  it("closes tasks overlay when ctrl key uses key.name without char payload", () => {
    const context = { ...baseContext(), todosOpen: false, tasksOpen: true }
    const handled = handleListOverlayKeys(context, makeInfo({ key: { ctrl: true, name: "b" } }))
    expect(handled).toBe(true)
    expect(context.setTasksOpen).toHaveBeenCalledWith(false)
  })

  it("enters focus lane mode on `f` from selected task", () => {
    const context = baseContext()
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "f", lowerChar: "f" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith("lane-a")
    expect(context.setTaskFocusFollowTail).toHaveBeenCalledWith(true)
    expect(context.setTaskFocusRawMode).toHaveBeenCalledWith(false)
    expect(context.setTaskFocusTailLines).toHaveBeenCalledWith(24)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(true)
    expect(context.setTaskIndex).toHaveBeenCalledWith(0)
    expect(context.setTaskScroll).toHaveBeenCalledWith(0)
    expect(context.requestTaskTail).toHaveBeenCalledWith({ raw: false, tailLines: 24 })
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

  it("returns from focus without disturbing task selection context", () => {
    const context = {
      ...baseContext(),
      taskFocusViewOpen: true,
      taskFocusLaneId: "lane-a",
      selectedTaskIndex: 3,
      taskScroll: 2,
    }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ key: { escape: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(false)
    expect(context.setTaskIndex).not.toHaveBeenCalled()
    expect(context.setTaskScroll).not.toHaveBeenCalled()
  })

  it("clears focused lane when exiting experimental swap mode", () => {
    const context = {
      ...baseContext(),
      taskFocusMode: "swap" as const,
      taskFocusViewOpen: true,
      taskFocusLaneId: "lane-a",
    }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ key: { escape: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(false)
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith(null)
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

  it("consumes printable slash-command residue during taskboard open quarantine", () => {
    const context = {
      ...baseContext(),
      taskboardInputQuarantineUntilRef: { current: Date.now() + 1000 },
    }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "a", lowerChar: "a" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskSearchQuery).not.toHaveBeenCalled()
  })

  it("still lets escape close taskboard during taskboard open quarantine", () => {
    const context = {
      ...baseContext(),
      taskboardInputQuarantineUntilRef: { current: Date.now() + 1000 },
    }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "\u001b", key: { escape: true } }),
    )
    expect(handled).toBe(true)
    expect(context.setTasksOpen).toHaveBeenCalledWith(false)
  })

  it("toggles group mode with `g`", () => {
    const context = baseContext()
    const handled = handleListOverlayKeys(context, makeInfo({ char: "g", lowerChar: "g" }))
    expect(handled).toBe(true)
    expect(context.setTaskGroupMode).toHaveBeenCalled()
    const reducer = context.setTaskGroupMode.mock.calls[0]?.[0]
    expect(typeof reducer).toBe("function")
    expect(reducer("status")).toBe("lane")
    expect(reducer("lane")).toBe("status")
  })

  it("cycles lane filter with `l` in taskboard list mode", () => {
    const context = { ...baseContext(), taskLaneFilter: "all", taskLaneOrder: ["lane-a", "lane-b"] }
    const handled = handleListOverlayKeys(context, makeInfo({ char: "l", lowerChar: "l" }))
    expect(handled).toBe(true)
    expect(context.setTaskLaneFilter).toHaveBeenCalledWith("lane-a")
  })

  it("toggles selected group collapse with `c`", () => {
    const context = baseContext()
    const handled = handleListOverlayKeys(context, makeInfo({ char: "c", lowerChar: "c" }))
    expect(handled).toBe(true)
    expect(context.setTaskCollapsedGroupKeys).toHaveBeenCalled()
    const reducer = context.setTaskCollapsedGroupKeys.mock.calls[0]?.[0]
    expect(typeof reducer).toBe("function")
    const collapsed = reducer(new Set<string>())
    expect(collapsed.has("status:running")).toBe(true)
    const expanded = reducer(new Set<string>(["status:running"]))
    expect(expanded.has("status:running")).toBe(false)
  })

  it("expands all groups with `e`", () => {
    const context = baseContext()
    const handled = handleListOverlayKeys(context, makeInfo({ char: "e", lowerChar: "e" }))
    expect(handled).toBe(true)
    expect(context.setTaskCollapsedGroupKeys).toHaveBeenCalledWith(new Set())
  })

  it("applies extended status filters for blocked/cancelled/pending", () => {
    const context = baseContext()
    expect(handleListOverlayKeys(context, makeInfo({ char: "4", lowerChar: "4" }))).toBe(true)
    expect(context.setTaskStatusFilter).toHaveBeenCalledWith("blocked")
    expect(handleListOverlayKeys(context, makeInfo({ char: "5", lowerChar: "5" }))).toBe(true)
    expect(context.setTaskStatusFilter).toHaveBeenCalledWith("cancelled")
    expect(handleListOverlayKeys(context, makeInfo({ char: "6", lowerChar: "6" }))).toBe(true)
    expect(context.setTaskStatusFilter).toHaveBeenCalledWith("pending")
  })

  it("jumps to top and bottom with home/end in task list mode", () => {
    const context = { ...baseContext(), taskRows: new Array(8).fill(0).map((_, i) => ({ id: `t-${i}`, task: { laneId: "lane-a" } })), taskMaxScroll: 4 }
    const homeHandled = handleListOverlayKeys(context, makeInfo({ isHomeKey: true }))
    expect(homeHandled).toBe(true)
    expect(context.setTaskIndex).toHaveBeenCalledWith(0)
    expect(context.setTaskScroll).toHaveBeenCalledWith(0)

    const endHandled = handleListOverlayKeys(context, makeInfo({ isEndKey: true }))
    expect(endHandled).toBe(true)
    expect(context.setTaskIndex).toHaveBeenCalledWith(7)
    expect(context.setTaskScroll).toHaveBeenCalledWith(4)
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

  it("toggles focus follow mode with `p`", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", taskFocusFollowTail: true }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "p", lowerChar: "p" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusFollowTail).toHaveBeenCalledWith(false)
  })

  it("resumes focus follow mode with `p` when paused", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", taskFocusFollowTail: false }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "p", lowerChar: "p" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusFollowTail).toHaveBeenCalledWith(true)
  })

  it("toggles raw mode with tab while focus view is open", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", taskFocusRawMode: false }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ key: { tab: true }, isTabKey: true }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusRawMode).toHaveBeenCalledWith(true)
    expect(context.requestTaskTail).toHaveBeenCalledWith({ raw: true, tailLines: 24 })
  })

  it("loads more lines in snippet mode with `l`", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", taskFocusRawMode: false, taskFocusTailLines: 24 }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "l", lowerChar: "l" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskFocusTailLines).toHaveBeenCalledWith(48)
    expect(context.requestTaskTail).toHaveBeenCalledWith({ raw: false, tailLines: 48, maxBytes: 80_000 })
  })

  it("triggers explicit refresh in focus mode with `r`", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", taskFocusRawMode: true, taskFocusTailLines: 80 }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "r", lowerChar: "r" }),
    )
    expect(handled).toBe(true)
    expect(context.requestTaskTail).toHaveBeenCalledWith({ raw: true, tailLines: 80 })
  })

  it("exports the selected task log in focus mode with `o`", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a" }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "o", lowerChar: "o" }),
    )
    expect(handled).toBe(true)
    expect(context.exportTaskLog).toHaveBeenCalled()
    expect(context.setTaskSearchQuery).not.toHaveBeenCalled()
  })

  it("renders an unavailable notice if task log export is not wired", () => {
    const context = {
      ...baseContext(),
      taskFocusViewOpen: true,
      taskFocusLaneId: "lane-a",
      exportTaskLog: undefined,
    }
    const handled = handleListOverlayKeys(
      context,
      makeInfo({ char: "o", lowerChar: "o" }),
    )
    expect(handled).toBe(true)
    expect(context.setTaskNotice).toHaveBeenCalledWith("Task log export unavailable.")
  })

  it("reports unavailable task mutation controls from focus mode", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a" }
    expect(handleListOverlayKeys(context, makeInfo({ char: "x", lowerChar: "x" }))).toBe(true)
    expect(context.setTaskActionNotice).toHaveBeenLastCalledWith(
      "cancel unavailable for task-1.",
    )
    expect(handleListOverlayKeys(context, makeInfo({ char: "y", lowerChar: "y" }))).toBe(true)
    expect(context.setTaskActionNotice).toHaveBeenLastCalledWith(
      "retry unavailable for task-1.",
    )
    expect(handleListOverlayKeys(context, makeInfo({ char: "u", lowerChar: "u" }))).toBe(true)
    expect(context.setTaskActionNotice).toHaveBeenLastCalledWith(
      "pause/resume unavailable for task-1.",
    )
    expect(handleListOverlayKeys(context, makeInfo({ char: "m", lowerChar: "m" }))).toBe(true)
    expect(context.setTaskActionNotice).toHaveBeenLastCalledWith(
      "merge unavailable for task-1.",
    )
  })

  it("dispatches task mutation controls from focus mode when wired", () => {
    const context = { ...baseContext(), taskFocusViewOpen: true, taskFocusLaneId: "lane-a", runTaskAction: vi.fn() }
    expect(handleListOverlayKeys(context, makeInfo({ char: "x", lowerChar: "x" }))).toBe(true)
    expect(context.runTaskAction).toHaveBeenLastCalledWith("cancel")
    expect(handleListOverlayKeys(context, makeInfo({ char: "y", lowerChar: "y" }))).toBe(true)
    expect(context.runTaskAction).toHaveBeenLastCalledWith("retry")
    expect(handleListOverlayKeys(context, makeInfo({ char: "u", lowerChar: "u" }))).toBe(true)
    expect(context.runTaskAction).toHaveBeenLastCalledWith("pause_resume")
    expect(handleListOverlayKeys(context, makeInfo({ char: "m", lowerChar: "m" }))).toBe(true)
    expect(context.runTaskAction).toHaveBeenLastCalledWith("merge")
    expect(context.setTaskActionNotice).not.toHaveBeenCalled()
  })

})
