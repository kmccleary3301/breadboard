import type { OverlayHandlerContext, OverlayHandlerResult, OverlayKeyInfo } from "./types.js"

export const handleListOverlayKeys = (
  context: OverlayHandlerContext,
  info: OverlayKeyInfo,
): OverlayHandlerResult => {
  const {
    todosOpen,
    setTodosOpen,
    keymap,
    todoMaxScroll,
    todoViewportRows,
    setTodoScroll,
    ctreeOpen,
    setCtreeOpen,
    ctreeRows,
    ctreeMaxScroll,
    ctreeScroll,
    ctreeViewportRows,
    setCtreeIndex,
    setCtreeScroll,
    selectedCTreeIndex,
    onCtreeRefresh,
    ctreeStage,
    ctreeIncludePreviews,
    ctreeSource,
    setCtreeShowDetails,
    setCtreeCollapsedNodes,
    ctreeCollapsibleIds,
    selectedCTreeRow,
    tasksOpen,
    setTasksOpen,
    taskMaxScroll,
    taskScroll,
    taskViewportRows,
    setTaskScroll,
    setTaskIndex,
    taskRows,
    taskLaneOrder,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    setTaskSearchQuery,
    setTaskStatusFilter,
    selectedTaskIndex,
    selectedTask,
    requestTaskTail,
    rewindMenu,
    rewindVisibleLimit,
    rewindIndex,
    setRewindIndex,
    onRewindClose,
    onRewindRestore,
    confirmState,
    closeConfirm,
    runConfirmAction,
  } = context
  const { char, key, lowerChar, isReturnKey, isTabKey, isCtrlT, isCtrlB, isCtrlY } = info

  if (todosOpen) {
    if (key.escape || char === "\u001b") {
      setTodosOpen(false)
      return true
    }
    if (isCtrlT && keymap === "claude") {
      setTodosOpen(false)
      return true
    }
    const clampScroll = (value: number) => Math.max(0, Math.min(todoMaxScroll, value))
    if (key.pageUp) {
      setTodoScroll((prev: number) => clampScroll(prev - todoViewportRows))
      return true
    }
    if (key.pageDown) {
      setTodoScroll((prev: number) => clampScroll(prev + todoViewportRows))
      return true
    }
    if (key.upArrow) {
      setTodoScroll((prev: number) => clampScroll(prev - 1))
      return true
    }
    if (key.downArrow) {
      setTodoScroll((prev: number) => clampScroll(prev + 1))
      return true
    }
    return true
  }

  if (ctreeOpen) {
    const lastIndex = Math.max(0, ctreeRows.length - 1)
    if (key.escape || char === "\u001b") {
      setCtreeOpen(false)
      return true
    }
    if (isCtrlY) {
      setCtreeOpen(false)
      return true
    }
    const clampScroll = (value: number) => Math.max(0, Math.min(ctreeMaxScroll, value))
    if (key.pageUp) {
      setCtreeIndex((prev: number) => Math.max(0, prev - ctreeViewportRows))
      setCtreeScroll((prev: number) => clampScroll(prev - ctreeViewportRows))
      return true
    }
    if (key.pageDown) {
      setCtreeIndex((prev: number) => Math.min(lastIndex, prev + ctreeViewportRows))
      setCtreeScroll((prev: number) => clampScroll(prev + ctreeViewportRows))
      return true
    }
    if (key.upArrow) {
      setCtreeIndex((prev: number) => Math.max(0, prev - 1))
      if (selectedCTreeIndex <= ctreeScroll) {
        setCtreeScroll((prev: number) => clampScroll(prev - 1))
      }
      return true
    }
    if (key.downArrow) {
      setCtreeIndex((prev: number) => Math.min(lastIndex, prev + 1))
      if (selectedCTreeIndex >= ctreeScroll + ctreeViewportRows - 1) {
        setCtreeScroll((prev: number) => clampScroll(prev + 1))
      }
      return true
    }
    if (!key.ctrl && !key.meta) {
      if (lowerChar === "r") {
        void onCtreeRefresh()
        return true
      }
      if (lowerChar === "s") {
        const stages = ["RAW", "SPEC", "HEADER", "FROZEN"]
        const current = stages.indexOf(ctreeStage.toUpperCase())
        const next = stages[(current + 1 + stages.length) % stages.length]
        void onCtreeRefresh({ stage: next })
        return true
      }
      if (lowerChar === "p") {
        void onCtreeRefresh({ includePreviews: !ctreeIncludePreviews })
        return true
      }
      if (lowerChar === "o") {
        const sources = ["auto", "memory", "eventlog", "disk"]
        const current = sources.indexOf(ctreeSource.toLowerCase())
        const next = sources[(current + 1 + sources.length) % sources.length]
        void onCtreeRefresh({ source: next })
        return true
      }
      if (lowerChar === "i") {
        setCtreeShowDetails((prev: boolean) => !prev)
        return true
      }
      if (lowerChar === "e") {
        setCtreeCollapsedNodes(new Set())
        return true
      }
      if (lowerChar === "c") {
        setCtreeCollapsedNodes(new Set(ctreeCollapsibleIds))
        return true
      }
    }
    if (isReturnKey) {
      if (selectedCTreeRow) {
        if (selectedCTreeRow.hasChildren) {
          const nodeId = selectedCTreeRow.id
          setCtreeCollapsedNodes((prev: Set<string>) => {
            const next = new Set(prev)
            if (next.has(nodeId)) next.delete(nodeId)
            else next.add(nodeId)
            return next
          })
        } else {
          setCtreeShowDetails((prev: boolean) => !prev)
        }
        return true
      }
      return true
    }
    return true
  }

  if (tasksOpen) {
    const lastIndex = Math.max(0, taskRows.length - 1)
    const normalizeLaneId = (value: unknown): string | null =>
      typeof value === "string" && value.trim().length > 0 ? value : null
    const laneOrder = Array.isArray(taskLaneOrder)
      ? taskLaneOrder.map((value: unknown) => normalizeLaneId(value)).filter(Boolean) as string[]
      : []
    const laneDelta = key.rightArrow || lowerChar === "]" ? 1 : key.leftArrow || lowerChar === "[" ? -1 : 0

    if (taskFocusViewOpen) {
      if (key.escape || char === "\u001b" || (!key.ctrl && !key.meta && lowerChar === "f")) {
        setTaskFocusViewOpen(false)
        return true
      }
      if (isCtrlB) {
        setTaskFocusViewOpen(false)
        setTasksOpen(false)
        return true
      }
      if (laneDelta !== 0 && laneOrder.length > 0) {
        const baseLane = normalizeLaneId(taskFocusLaneId) ?? laneOrder[0] ?? null
        const currentIndex = Math.max(0, laneOrder.indexOf(baseLane ?? ""))
        const nextIndex = (currentIndex + laneDelta + laneOrder.length) % laneOrder.length
        setTaskFocusLaneId(laneOrder[nextIndex] ?? null)
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      const clampScroll = (value: number) => Math.max(0, Math.min(taskMaxScroll, value))
      if (key.pageUp) {
        setTaskScroll((prev: number) => clampScroll(prev - taskViewportRows))
        setTaskIndex((prev: number) => Math.max(0, prev - taskViewportRows))
        return true
      }
      if (key.pageDown) {
        setTaskScroll((prev: number) => clampScroll(prev + taskViewportRows))
        setTaskIndex((prev: number) => Math.min(lastIndex, prev + taskViewportRows))
        return true
      }
      if (key.upArrow) {
        setTaskIndex((prev: number) => Math.max(0, prev - 1))
        if (selectedTaskIndex <= taskScroll) {
          setTaskScroll((prev: number) => clampScroll(prev - 1))
        }
        return true
      }
      if (key.downArrow) {
        setTaskIndex((prev: number) => Math.min(lastIndex, prev + 1))
        if (selectedTaskIndex >= taskScroll + taskViewportRows - 1) {
          setTaskScroll((prev: number) => clampScroll(prev + 1))
        }
        return true
      }
      if (key.return) {
        void requestTaskTail({ raw: taskFocusRawMode, tailLines: taskFocusTailLines })
        return true
      }
      if (isTabKey) {
        const nextRawMode = !taskFocusRawMode
        setTaskFocusRawMode(nextRawMode)
        void requestTaskTail({ raw: nextRawMode, tailLines: taskFocusTailLines })
        return true
      }
      if (!key.ctrl && !key.meta && lowerChar === "l" && !taskFocusRawMode) {
        const nextTailLines = Math.max(8, Math.min(400, taskFocusTailLines + 24))
        setTaskFocusTailLines(nextTailLines)
        void requestTaskTail({ raw: false, tailLines: nextTailLines, maxBytes: 80_000 })
        return true
      }
      if (!key.ctrl && !key.meta && lowerChar === "p") {
        setTaskFocusFollowTail(!taskFocusFollowTail)
        return true
      }
      if (!key.ctrl && !key.meta && lowerChar === "r") {
        void requestTaskTail({ raw: taskFocusRawMode, tailLines: taskFocusTailLines })
        return true
      }
      return true
    }

    if (key.escape || char === "\u001b") {
      setTasksOpen(false)
      return true
    }
    if (isCtrlB) {
      setTasksOpen(false)
      return true
    }
    if (!key.ctrl && !key.meta && lowerChar === "f") {
      const preferredLane = normalizeLaneId(selectedTask?.laneId) ?? laneOrder[0] ?? null
      if (preferredLane) {
        setTaskFocusLaneId(preferredLane)
        setTaskFocusFollowTail(true)
        setTaskFocusRawMode(false)
        setTaskFocusTailLines(24)
        setTaskFocusViewOpen(true)
        setTaskIndex(0)
        setTaskScroll(0)
        void requestTaskTail({ raw: false, tailLines: 24 })
      }
      return true
    }
    const clampScroll = (value: number) => Math.max(0, Math.min(taskMaxScroll, value))
    if (key.pageUp) {
      setTaskScroll((prev: number) => clampScroll(prev - taskViewportRows))
      setTaskIndex((prev: number) => Math.max(0, prev - taskViewportRows))
      return true
    }
    if (key.pageDown) {
      setTaskScroll((prev: number) => clampScroll(prev + taskViewportRows))
      setTaskIndex((prev: number) => Math.min(lastIndex, prev + taskViewportRows))
      return true
    }
    if (key.ctrl && lowerChar === "u") {
      setTaskSearchQuery("")
      setTaskIndex(0)
      setTaskScroll(0)
      return true
    }
    if (key.backspace || key.delete) {
      setTaskSearchQuery((prev: string) => prev.slice(0, -1))
      setTaskIndex(0)
      setTaskScroll(0)
      return true
    }
    if (!key.ctrl && !key.meta) {
      if (lowerChar === "0") {
        setTaskStatusFilter("all")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "1") {
        setTaskStatusFilter("running")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "2") {
        setTaskStatusFilter("completed")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "3") {
        setTaskStatusFilter("failed")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
    }
    if (key.upArrow) {
      setTaskIndex((prev: number) => Math.max(0, prev - 1))
      if (selectedTaskIndex <= taskScroll) {
        setTaskScroll((prev: number) => clampScroll(prev - 1))
      }
      return true
    }
    if (key.downArrow) {
      setTaskIndex((prev: number) => Math.min(lastIndex, prev + 1))
      if (selectedTaskIndex >= taskScroll + taskViewportRows - 1) {
        setTaskScroll((prev: number) => clampScroll(prev + 1))
      }
      return true
    }
    if (key.return) {
      void requestTaskTail()
      return true
    }
    if (char && char.length > 0 && !key.ctrl && !key.meta && !key.return && !key.escape) {
      setTaskSearchQuery((prev: string) => prev + char)
      setTaskIndex(0)
      setTaskScroll(0)
      return true
    }
    return true
  }

  if (rewindMenu.status !== "hidden") {
    const checkpoints = rewindMenu.checkpoints
    if (key.escape) {
      onRewindClose()
      return true
    }
    if (key.pageUp || key.pageDown) {
      const jump = Math.max(1, rewindVisibleLimit)
      const delta = key.pageUp ? -jump : jump
      setRewindIndex((prev: number) => {
        if (checkpoints.length === 0) return 0
        const next = Math.max(0, Math.min(checkpoints.length - 1, prev + delta))
        return next
      })
      return true
    }
    if (key.upArrow) {
      setRewindIndex((prev: number) => (checkpoints.length === 0 ? 0 : Math.max(0, prev - 1)))
      return true
    }
    if (key.downArrow) {
      setRewindIndex((prev: number) =>
        checkpoints.length === 0 ? 0 : Math.min(checkpoints.length - 1, prev + 1),
      )
      return true
    }
    const current = checkpoints[Math.max(0, Math.min(rewindIndex, checkpoints.length - 1))]
    if (current) {
      if (lowerChar === "1") {
        void onRewindRestore(current.checkpointId, "conversation")
        return true
      }
      if (lowerChar === "2") {
        void onRewindRestore(current.checkpointId, "code")
        return true
      }
      if (lowerChar === "3") {
        void onRewindRestore(current.checkpointId, "both")
        return true
      }
    }
    return true
  }

  if (confirmState.status === "prompt") {
    if (key.escape) {
      closeConfirm()
      return true
    }
    if (isReturnKey) {
      runConfirmAction()
      return true
    }
    return true
  }

  return undefined
}
