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
    taskFocusDefaultTailLines,
    taskFocusMode,
    taskLaneFilter,
    taskGroupMode,
    taskCollapsedGroupKeys,
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    setTaskLaneFilter,
    setTaskGroupMode,
    setTaskCollapsedGroupKeys,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskNotice,
    setTaskActionNotice,
    taskboardInputQuarantineUntilRef,
    selectedTaskIndex,
    selectedTaskRow,
    selectedTask,
    requestTaskTail,
    exportTaskLog,
    runTaskAction,
    rewindMenu,
    rewindVisibleLimit,
    rewindIndex,
    setRewindIndex,
    onRewindClose,
    onRewindRestore,
    recentSessionsOpen,
    recentSessionsRows,
    recentSessionsIndex,
    recentSessionsScroll,
    recentSessionsMaxScroll,
    recentSessionsViewportRows,
    recentSessionsOpenedAtRef,
    setRecentSessionsOpen,
    setRecentSessionsIndex,
    setRecentSessionsScroll,
    refreshRecentSessions,
    attachRecentSession,
    collapsedDetailOpen,
    collapsedDetailScroll,
    collapsedDetailMaxScroll,
    collapsedDetailViewportRows,
    setCollapsedDetailOpen,
    setCollapsedDetailScroll,
    confirmState,
    closeConfirm,
    runConfirmAction,
    resultDetailOpen,
    resultDetailScroll,
    resultDetailMaxScroll,
    resultDetailViewportRows,
    resultDetailArtifactPath,
    resultDetailSourceLine,
    setResultDetailOpen,
    setResultDetailScroll,
    openSelectedTranscriptArtifactPreview,
    artifactPreviewOpen,
    artifactPreviewScroll,
    artifactPreviewMaxScroll,
    artifactPreviewViewportRows,
    artifactPreviewSourceLine,
    setArtifactPreviewOpen,
    setArtifactPreviewScroll,
    jumpTranscriptToLine,
  } = context
  const { char, key, lowerChar, isReturnKey, isTabKey, isCtrlT, isCtrlB, isCtrlY, isHomeKey, isEndKey } = info
  const keyName = typeof (key as Record<string, unknown>).name === "string"
    ? String((key as Record<string, unknown>).name).toLowerCase()
    : ""
  const isCtrlTKey = isCtrlT || char === "\u0014" || (key.ctrl && (lowerChar === "t" || keyName === "t"))
  const isCtrlBKey = isCtrlB || char === "\u0002" || (key.ctrl && (lowerChar === "b" || keyName === "b"))

  if (artifactPreviewOpen) {
    const clampScroll = (value: number) => Math.max(0, Math.min(artifactPreviewMaxScroll, value))
    if (!key.ctrl && !key.meta && lowerChar === "j" && Number.isFinite(artifactPreviewSourceLine)) {
      setArtifactPreviewOpen(false)
      setResultDetailOpen(false)
      jumpTranscriptToLine(Number(artifactPreviewSourceLine))
      return true
    }
    if (key.escape || char === "\u001b") {
      setArtifactPreviewOpen(false)
      return true
    }
    if (key.pageUp) {
      setArtifactPreviewScroll((prev: number) => clampScroll(prev - artifactPreviewViewportRows))
      return true
    }
    if (key.pageDown) {
      setArtifactPreviewScroll((prev: number) => clampScroll(prev + artifactPreviewViewportRows))
      return true
    }
    if (key.upArrow) {
      setArtifactPreviewScroll((prev: number) => clampScroll(prev - 1))
      return true
    }
    if (key.downArrow) {
      setArtifactPreviewScroll((prev: number) => clampScroll(prev + 1))
      return true
    }
    if (isHomeKey || (!key.ctrl && !key.meta && lowerChar === "g")) {
      setArtifactPreviewScroll(0)
      return true
    }
    if (isEndKey || (!key.ctrl && !key.meta && char === "G")) {
      setArtifactPreviewScroll(artifactPreviewMaxScroll)
      return true
    }
    return true
  }

  if (resultDetailOpen) {
    const clampScroll = (value: number) => Math.max(0, Math.min(resultDetailMaxScroll, value))
    if (!key.ctrl && !key.meta && lowerChar === "j" && Number.isFinite(resultDetailSourceLine)) {
      setResultDetailOpen(false)
      jumpTranscriptToLine(Number(resultDetailSourceLine))
      return true
    }
    if (key.escape || char === "\u001b") {
      setResultDetailOpen(false)
      return true
    }
    if (isReturnKey && resultDetailArtifactPath) {
      return openSelectedTranscriptArtifactPreview()
    }
    if (key.pageUp) {
      setResultDetailScroll((prev: number) => clampScroll(prev - resultDetailViewportRows))
      return true
    }
    if (key.pageDown) {
      setResultDetailScroll((prev: number) => clampScroll(prev + resultDetailViewportRows))
      return true
    }
    if (key.upArrow) {
      setResultDetailScroll((prev: number) => clampScroll(prev - 1))
      return true
    }
    if (key.downArrow) {
      setResultDetailScroll((prev: number) => clampScroll(prev + 1))
      return true
    }
    if (isHomeKey || (!key.ctrl && !key.meta && lowerChar === "g")) {
      setResultDetailScroll(0)
      return true
    }
    if (isEndKey || (!key.ctrl && !key.meta && char === "G")) {
      setResultDetailScroll(resultDetailMaxScroll)
      return true
    }
    return true
  }

  if (recentSessionsOpen) {
    const lastIndex = Math.max(0, recentSessionsRows.length - 1)
    const clampScroll = (value: number) => Math.max(0, Math.min(recentSessionsMaxScroll, value))
    if (key.escape || char === "\u001b") {
      setRecentSessionsOpen(false)
      return true
    }
    if (!key.ctrl && !key.meta && lowerChar === "r") {
      void refreshRecentSessions()
      return true
    }
    if (isReturnKey) {
      const openedAt = recentSessionsOpenedAtRef?.current
      if (openedAt && Date.now() - openedAt < 200) {
        return true
      }
      if (recentSessionsOpenedAtRef) {
        recentSessionsOpenedAtRef.current = null
      }
      const selected = recentSessionsRows[recentSessionsIndex]
      if (selected?.sessionId) {
        void attachRecentSession(selected.sessionId)
      }
      return true
    }
    if (key.pageUp) {
      setRecentSessionsIndex((prev: number) => Math.max(0, prev - recentSessionsViewportRows))
      setRecentSessionsScroll((prev: number) => clampScroll(prev - recentSessionsViewportRows))
      return true
    }
    if (key.pageDown) {
      setRecentSessionsIndex((prev: number) => Math.min(lastIndex, prev + recentSessionsViewportRows))
      setRecentSessionsScroll((prev: number) => clampScroll(prev + recentSessionsViewportRows))
      return true
    }
    if (key.upArrow) {
      setRecentSessionsIndex((prev: number) => Math.max(0, prev - 1))
      if (recentSessionsIndex <= recentSessionsScroll) {
        setRecentSessionsScroll((prev: number) => clampScroll(prev - 1))
      }
      return true
    }
    if (key.downArrow) {
      setRecentSessionsIndex((prev: number) => Math.min(lastIndex, prev + 1))
      if (recentSessionsIndex >= recentSessionsScroll + recentSessionsViewportRows - 1) {
        setRecentSessionsScroll((prev: number) => clampScroll(prev + 1))
      }
      return true
    }
    if (isHomeKey) {
      setRecentSessionsIndex(0)
      setRecentSessionsScroll(0)
      return true
    }
    if (isEndKey) {
      setRecentSessionsIndex(lastIndex)
      setRecentSessionsScroll(recentSessionsMaxScroll)
      return true
    }
    return true
  }

  if (collapsedDetailOpen) {
    const clampScroll = (value: number) => Math.max(0, Math.min(collapsedDetailMaxScroll, value))
    if (key.escape || char === "\u001b") {
      setCollapsedDetailOpen(false)
      return true
    }
    if (key.pageUp) {
      setCollapsedDetailScroll((prev: number) => clampScroll(prev - collapsedDetailViewportRows))
      return true
    }
    if (key.pageDown) {
      setCollapsedDetailScroll((prev: number) => clampScroll(prev + collapsedDetailViewportRows))
      return true
    }
    if (key.upArrow) {
      setCollapsedDetailScroll((prev: number) => clampScroll(prev - 1))
      return true
    }
    if (key.downArrow) {
      setCollapsedDetailScroll((prev: number) => clampScroll(prev + 1))
      return true
    }
    if (isHomeKey || (!key.ctrl && !key.meta && lowerChar === "g")) {
      setCollapsedDetailScroll(0)
      return true
    }
    if (isEndKey || (!key.ctrl && !key.meta && char === "G")) {
      setCollapsedDetailScroll(collapsedDetailMaxScroll)
      return true
    }
    return true
  }

  if (todosOpen) {
    if (key.escape || char === "\u001b") {
      if (typeof setTodosOpen === "function") {
        setTodosOpen(false)
      }
      return true
    }
    if (isCtrlTKey && keymap === "claude") {
      if (typeof setTodosOpen === "function") {
        setTodosOpen(false)
      }
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
    const focusMode = taskFocusMode === "swap" ? "swap" : "lane"
    const lastIndex = Math.max(0, taskRows.length - 1)
    const normalizeLaneId = (value: unknown): string | null =>
      typeof value === "string" && value.trim().length > 0 ? value : null
    const laneOrder = Array.isArray(taskLaneOrder)
      ? taskLaneOrder.map((value: unknown) => normalizeLaneId(value)).filter(Boolean) as string[]
      : []
    const cycleLaneFilter = () => {
      const options = ["all", ...laneOrder]
      if (options.length <= 1) {
        setTaskLaneFilter("all")
        return
      }
      const current = normalizeLaneId(taskLaneFilter) ?? "all"
      const currentIndex = Math.max(0, options.indexOf(current))
      const nextIndex = (currentIndex + 1) % options.length
      setTaskLaneFilter(options[nextIndex] ?? "all")
    }
    const laneDelta = key.rightArrow || lowerChar === "]" ? 1 : key.leftArrow || lowerChar === "[" ? -1 : 0
    const setTaskActionUnavailableNotice = (action: "pause" | "resume" | "cancel" | "retry" | "merge") => {
      const taskId =
        typeof selectedTask?.id === "string" && selectedTask.id.trim().length > 0
          ? selectedTask.id.trim()
          : typeof selectedTaskRow?.id === "string" && selectedTaskRow.id.trim().length > 0
            ? selectedTaskRow.id.trim()
            : null
      const target = taskId ? ` for ${taskId}` : ""
      const label = action === "resume" ? "pause/resume" : action
      const notice = `${label} unavailable${target}.`
      if (typeof setTaskActionNotice === "function") {
        setTaskActionNotice(notice)
      } else if (typeof setTaskNotice === "function") {
        setTaskNotice(notice)
      }
    }
    const handleTaskMutationKey = (): boolean => {
      if (key.ctrl || key.meta) return false
      if (lowerChar === "x") {
        if (typeof runTaskAction === "function") void runTaskAction("cancel")
        else setTaskActionUnavailableNotice("cancel")
        return true
      }
      if (lowerChar === "y") {
        if (typeof runTaskAction === "function") void runTaskAction("retry")
        else setTaskActionUnavailableNotice("retry")
        return true
      }
      if (lowerChar === "u") {
        if (typeof runTaskAction === "function") void runTaskAction("pause_resume")
        else setTaskActionUnavailableNotice("resume")
        return true
      }
      if (lowerChar === "m") {
        if (typeof runTaskAction === "function") void runTaskAction("merge")
        else setTaskActionUnavailableNotice("merge")
        return true
      }
      return false
    }
    const quarantineUntil = Number(taskboardInputQuarantineUntilRef?.current ?? 0)
    const inOpenQuarantine = quarantineUntil > Date.now() && !key.ctrl && !key.meta
    const isOpenResidue =
      Boolean(char && char.length > 0) ||
      isReturnKey ||
      isTabKey ||
      key.backspace ||
      key.delete
    if (inOpenQuarantine && isOpenResidue && !key.escape) {
      return true
    }

    if (taskFocusViewOpen) {
      if (key.escape || char === "\u001b" || (!key.ctrl && !key.meta && lowerChar === "f")) {
        setTaskFocusViewOpen(false)
        if (focusMode === "swap") {
          setTaskFocusLaneId(null)
        }
        return true
      }
      if (isCtrlBKey) {
        setTaskFocusViewOpen(false)
        if (typeof setTasksOpen === "function") {
          setTasksOpen(false)
        }
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
      if (isHomeKey) {
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (isEndKey) {
        setTaskIndex(lastIndex)
        setTaskScroll(taskMaxScroll)
        return true
      }
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
      if (isReturnKey) {
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
      if (!key.ctrl && !key.meta && lowerChar === "o") {
        if (typeof exportTaskLog === "function") {
          void exportTaskLog()
        } else {
          setTaskNotice("Task log export unavailable.")
        }
        return true
      }
      if (handleTaskMutationKey()) {
        return true
      }
      return true
    }

    if (key.escape || char === "\u001b") {
      if (typeof setTasksOpen === "function") {
        setTasksOpen(false)
      }
      return true
    }
    if (isCtrlBKey) {
      if (typeof setTasksOpen === "function") {
        setTasksOpen(false)
      }
      return true
    }
    if (!key.ctrl && !key.meta && lowerChar === "f") {
      const preferredLane = normalizeLaneId(selectedTask?.laneId) ?? laneOrder[0] ?? null
      if (preferredLane) {
        setTaskFocusLaneId(preferredLane)
        setTaskFocusFollowTail(true)
        setTaskFocusRawMode(false)
        setTaskFocusTailLines(taskFocusDefaultTailLines)
        setTaskFocusViewOpen(true)
        setTaskIndex(0)
        setTaskScroll(0)
        void requestTaskTail({ raw: false, tailLines: taskFocusDefaultTailLines })
      }
      return true
    }
    const clampScroll = (value: number) => Math.max(0, Math.min(taskMaxScroll, value))
    if (isHomeKey) {
      setTaskIndex(0)
      setTaskScroll(0)
      return true
    }
    if (isEndKey) {
      setTaskIndex(lastIndex)
      setTaskScroll(taskMaxScroll)
      return true
    }
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
      if (lowerChar === "g") {
        setTaskGroupMode((prev: "status" | "lane") => (prev === "status" ? "lane" : "status"))
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "l") {
        cycleLaneFilter()
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "c") {
        const groupKey =
          typeof selectedTaskRow?.groupKey === "string" && selectedTaskRow.groupKey.length > 0
            ? selectedTaskRow.groupKey
            : null
        if (groupKey) {
          setTaskCollapsedGroupKeys((prev: Set<string>) => {
            const base = prev instanceof Set ? prev : new Set(taskCollapsedGroupKeys ?? [])
            const next = new Set(base)
            if (next.has(groupKey)) next.delete(groupKey)
            else next.add(groupKey)
            return next
          })
          setTaskIndex(0)
          setTaskScroll(0)
        }
        return true
      }
      if (lowerChar === "e") {
        setTaskCollapsedGroupKeys(new Set())
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
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
      if (lowerChar === "4") {
        setTaskStatusFilter("blocked")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "5") {
        setTaskStatusFilter("cancelled")
        setTaskIndex(0)
        setTaskScroll(0)
        return true
      }
      if (lowerChar === "6") {
        setTaskStatusFilter("pending")
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
    if (isReturnKey) {
      void requestTaskTail()
      return true
    }
    if (char && char.length > 0 && !key.ctrl && !key.meta && !isReturnKey && !key.escape) {
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
