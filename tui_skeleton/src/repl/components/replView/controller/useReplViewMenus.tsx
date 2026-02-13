import { useCallback, useEffect, useMemo } from "react"
import path from "node:path"
import { promises as fs } from "node:fs"
import { scoreFuzzyMatch } from "../../../fileRanking.js"

type MenusContext = Record<string, any>

export const useReplViewMenus = (context: MenusContext) => {
  const {
    skillsMenu,
    skillsSearch,
    skillsIndex,
    skillsOffset,
    setSkillsIndex,
    setSkillsOffset,
    setSkillsSearch,
    skillsMode,
    setSkillsMode,
    skillsSelected,
    setSkillsSelected,
    setSkillsDirty,
    onSkillsApply,
    onSkillsMenuCancel,
    SKILL_GROUP_ORDER,
    buildSkillKey,
    skillsVisibleRows,
    MODEL_PROVIDER_ORDER,
    modelMenu,
    modelSearch,
    modelProviderFilter,
    setModelSearch,
    setModelIndex,
    setModelOffset,
    setModelProviderFilter,
    normalizeProviderKey,
    formatProviderLabel,
    MODEL_VISIBLE_ROWS,
    permissionRequest,
    setPermissionScope,
    ALWAYS_ALLOW_SCOPE,
    setPermissionScroll,
    setPermissionFileIndex,
    setPermissionNote,
    setPermissionNoteCursor,
    permissionTabRef,
    setPermissionTab,
    permissionTab,
    permissionNoteRef,
    permissionNote,
    permissionDecisionTimerRef,
    tasksOpen,
    setTaskIndex,
    setTaskScroll,
    setTaskNotice,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskTailLines,
    setTaskTailPath,
    setTaskFocusLaneId,
    taskRows,
    taskMaxScroll,
    ctreeOpen,
    setCtreeIndex,
    setCtreeScroll,
    setCtreeShowDetails,
    setCtreeCollapsedNodes,
    ctreeRows,
    ctreeMaxScroll,
    onCtreeRequest,
    sessionId,
    todosOpen,
    setTodoScroll,
    todos,
    rewindMenu,
    setRewindIndex,
    input,
    suggestions,
    setSuggestIndex,
    escPrimedAt,
    setEscPrimedAt,
    ctrlCPrimedAt,
    setCtrlCPrimedAt,
    DOUBLE_CTRL_C_WINDOW_MS,
    stdout,
    transcriptViewerOpen,
    setTranscriptViewerOpen,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptSearchIndex,
    setTranscriptExportNotice,
    transcriptViewerLines,
    pushCommandResult,
  } = context

  const safeSetSkillsSearch = useCallback(
    (value: string) => {
      if (typeof setSkillsSearch === "function") {
        setSkillsSearch(value)
      }
    },
    [setSkillsSearch],
  )

  const skillsCatalog = useMemo(() => (skillsMenu.status === "ready" ? skillsMenu.catalog : null), [skillsMenu])
  const skillsSelection = useMemo(
    () => (skillsMenu.status === "ready" ? skillsMenu.selection : null),
    [skillsMenu],
  )
  const skillsSources = useMemo(
    () => (skillsMenu.status === "ready" ? skillsMenu.sources ?? null : null),
    [skillsMenu],
  )

  const skillsData = useMemo(() => {
    const entries = skillsCatalog?.skills ?? []
    const query = skillsSearch.trim().toLowerCase()
    const normalizeGroup = (value?: string | null) => {
      const normalized = value?.trim().toLowerCase() ?? ""
      return normalized || "misc"
    }
    const formatGroupLabel = (group: string) =>
      group
        .split(/[^a-z0-9]+/i)
        .filter(Boolean)
        .map((part) => part[0]?.toUpperCase() + part.slice(1))
        .join(" ")
    type SkillRow =
      | { kind: "header"; label: string }
      | { kind: "item"; entry: any; index: number }
    if (!query) {
      const grouped = new Map<string, any[]>()
      for (const entry of entries) {
        const key = normalizeGroup(entry.group)
        if (!grouped.has(key)) grouped.set(key, [])
        grouped.get(key)!.push(entry)
      }
      const order: string[] = []
      for (const group of SKILL_GROUP_ORDER) {
        if (grouped.has(group)) order.push(group)
      }
      for (const group of grouped.keys()) {
        if (!order.includes(group)) order.push(group)
      }
      const items: any[] = []
      const rows: SkillRow[] = []
      for (const group of order) {
        const groupEntries = grouped.get(group) ?? []
        groupEntries.sort((a, b) => {
          const al = (a.label ?? a.id).toLowerCase()
          const bl = (b.label ?? b.id).toLowerCase()
          if (al !== bl) return al.localeCompare(bl)
          return a.version.localeCompare(b.version)
        })
        if (groupEntries.length === 0) continue
        rows.push({ kind: "header", label: formatGroupLabel(group) })
        for (const entry of groupEntries) {
          const index = items.length
          items.push(entry)
          rows.push({ kind: "item", entry, index })
        }
      }
      return { items, rows }
    }
    const scored = entries
      .map((entry: any) => {
        const candidate = [
          entry.id,
          entry.label ?? "",
          entry.group ?? "",
          entry.description ?? "",
          ...(entry.tags ?? []),
        ].join(" ")
        const score = scoreFuzzyMatch(candidate, query)
        return score == null ? null : { entry, score }
      })
      .filter((item: any): item is { entry: any; score: number } => item != null)
    scored.sort((a: any, b: any) => {
      if (b.score !== a.score) return b.score - a.score
      const al = (a.entry.label ?? a.entry.id).toLowerCase()
      const bl = (b.entry.label ?? b.entry.id).toLowerCase()
      if (al !== bl) return al.localeCompare(bl)
      return a.entry.version.localeCompare(b.entry.version)
    })
    const items = scored.map((item: any) => item.entry)
    const rows: SkillRow[] = items.map((entry: any, index: number) => ({ kind: "item", entry, index }))
    return { items, rows }
  }, [SKILL_GROUP_ORDER, skillsCatalog, skillsSearch])

  const skillsSelectedEntry = useMemo(() => {
    if (skillsData.items.length === 0) return null
    const safeIndex = Math.max(0, Math.min(skillsIndex, skillsData.items.length - 1))
    return skillsData.items[safeIndex] ?? null
  }, [skillsData.items, skillsIndex])

  const skillsDisplayRows = useMemo(() => skillsData.rows, [skillsData.rows])

  const skillsDisplayIndex = useMemo(() => {
    if (!skillsSelectedEntry) return 0
    const target = skillsData.items.indexOf(skillsSelectedEntry)
    if (target < 0) return 0
    const rowIndex = skillsDisplayRows.findIndex((row) => row.kind === "item" && row.index === target)
    return rowIndex >= 0 ? rowIndex : 0
  }, [skillsData.items, skillsDisplayRows, skillsSelectedEntry])

  useEffect(() => {
    if (skillsMenu.status === "hidden") return
    if (skillsData.items.length === 0) {
      setSkillsIndex(0)
      setSkillsOffset(0)
      return
    }
    if (skillsIndex >= skillsData.items.length) {
      setSkillsIndex(0)
    }
    if (skillsDisplayIndex < skillsOffset) {
      setSkillsOffset(skillsDisplayIndex)
    } else if (skillsDisplayIndex >= skillsOffset + skillsVisibleRows) {
      setSkillsOffset(Math.max(0, skillsDisplayIndex - skillsVisibleRows + 1))
    }
  }, [skillsData.items.length, skillsDisplayIndex, skillsIndex, skillsMenu.status, skillsOffset, skillsVisibleRows])

  const resetSkillsSelection = useCallback(
    (modeOverride?: "allowlist" | "blocklist") => {
      const mode = modeOverride ?? skillsMode
      const base = skillsSelection ?? {}
      const source = mode === "allowlist" ? base.allowlist ?? [] : base.blocklist ?? []
      const normalized = source.filter((item: any): item is string => typeof item === "string" && item.length > 0)
      setSkillsMode(mode)
      setSkillsSelected(new Set(normalized))
      setSkillsDirty(false)
    },
    [skillsMode, skillsSelection, setSkillsDirty, setSkillsMode, setSkillsSelected],
  )

  const deriveSelectionFromCatalog = useCallback(
    (mode: "allowlist" | "blocklist") => {
      const entries = skillsCatalog?.skills ?? []
      const next = new Set<string>()
      for (const entry of entries) {
        const enabled = entry.enabled !== false
        if (mode === "allowlist") {
          if (enabled) next.add(buildSkillKey(entry))
        } else {
          if (!enabled) next.add(buildSkillKey(entry))
        }
      }
      return next
    },
    [buildSkillKey, skillsCatalog],
  )

  const toggleSkillsMode = useCallback(() => {
    const nextMode = skillsMode === "allowlist" ? "blocklist" : "allowlist"
    const derived = deriveSelectionFromCatalog(nextMode)
    setSkillsMode(nextMode)
    setSkillsSelected(derived)
    setSkillsDirty(true)
  }, [deriveSelectionFromCatalog, setSkillsDirty, setSkillsMode, setSkillsSelected, skillsMode])

  const toggleSkillSelection = useCallback(
    (entry: any) => {
      setSkillsSelected((prev: Set<string>) => {
        const next = new Set(prev)
        const key = buildSkillKey(entry)
        if (next.has(entry.id) || next.has(key)) {
          next.delete(entry.id)
          next.delete(key)
        } else {
          next.add(key)
        }
        return next
      })
      setSkillsDirty(true)
    },
    [buildSkillKey, setSkillsDirty, setSkillsSelected],
  )

  const applySkillsSelection = useCallback(async () => {
    if (skillsMenu.status !== "ready") return
    const list = Array.from(skillsSelected)
    const selection =
      skillsMode === "allowlist" ? { mode: skillsMode, allowlist: list } : { mode: skillsMode, blocklist: list }
    await onSkillsApply(selection)
    onSkillsMenuCancel()
  }, [onSkillsApply, onSkillsMenuCancel, skillsMenu.status, skillsMode, skillsSelected])

  const filteredModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const query = modelSearch.trim().toLowerCase()
    const base = modelProviderFilter
      ? modelMenu.items.filter((item: any) => normalizeProviderKey(item.provider) === modelProviderFilter)
      : modelMenu.items
    const groups = new Map<string, any[]>()
    const order: string[] = []
    if (query.length === 0) {
      for (const item of base) {
        const key = normalizeProviderKey(item.provider)
        if (!groups.has(key)) {
          groups.set(key, [])
        }
        groups.get(key)?.push(item)
      }
      for (const provider of MODEL_PROVIDER_ORDER ?? []) {
        if (groups.has(provider)) order.push(provider)
      }
      for (const provider of groups.keys()) {
        if (!order.includes(provider)) order.push(provider)
      }
    } else {
      const scored = new Map<string, Array<{ item: any; score: number }>>()
      for (const item of base) {
        const candidate = `${item.provider} ${item.label} ${item.value}`
        const score = scoreFuzzyMatch(candidate, query)
        if (score == null) continue
        const key = normalizeProviderKey(item.provider)
        if (!scored.has(key)) scored.set(key, [])
        scored.get(key)?.push({ item, score })
      }
      for (const item of base) {
        const key = normalizeProviderKey(item.provider)
        if (!scored.has(key)) continue
        if (!order.includes(key)) order.push(key)
      }
      for (const [key, entries] of scored.entries()) {
        entries.sort((a, b) => {
          if (b.score !== a.score) return b.score - a.score
          if (a.item.label.length !== b.item.label.length) return a.item.label.length - b.item.label.length
          return a.item.label.localeCompare(b.item.label)
        })
        groups.set(
          key,
          entries.map((entry) => entry.item),
        )
      }
      const ordered: string[] = []
      for (const provider of MODEL_PROVIDER_ORDER ?? []) {
        if (groups.has(provider)) ordered.push(provider)
      }
      for (const provider of order) {
        if (!ordered.includes(provider)) ordered.push(provider)
      }
      order.splice(0, order.length, ...ordered)
    }
    const grouped: any[] = []
    for (const provider of order) {
      const items = groups.get(provider)
      if (items) grouped.push(...items)
    }
    return grouped
  }, [MODEL_PROVIDER_ORDER, modelMenu, modelSearch, modelProviderFilter, normalizeProviderKey])

  const modelProviderOrder = useMemo(() => {
    const items = modelMenu.status === "ready" ? modelMenu.items : []
    const present = new Set<string>()
    for (const item of items) {
      present.add(normalizeProviderKey(item.provider))
    }
    const ordered: string[] = []
    for (const provider of MODEL_PROVIDER_ORDER ?? []) {
      if (present.has(provider)) ordered.push(provider)
    }
    for (const provider of present) {
      if (!ordered.includes(provider)) ordered.push(provider)
    }
    return ordered
  }, [MODEL_PROVIDER_ORDER, modelMenu, normalizeProviderKey])

  const modelProviderLabel = useMemo(() => {
    if (!modelProviderFilter) return "All"
    return formatProviderLabel(modelProviderFilter)
  }, [modelProviderFilter, formatProviderLabel])

  const modelProviderCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of filteredModels) {
      const key = normalizeProviderKey(item.provider)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return counts
  }, [filteredModels, normalizeProviderKey])

  useEffect(() => {
    if (modelMenu.status === "hidden") {
      setModelSearch("")
      setModelIndex(0)
      setModelOffset(0)
      setModelProviderFilter(null)
    }
  }, [modelMenu.status, setModelIndex, setModelOffset, setModelProviderFilter, setModelSearch])

  useEffect(() => {
    if (skillsMenu.status === "hidden") {
      safeSetSkillsSearch("")
      setSkillsIndex(0)
      setSkillsOffset(0)
      setSkillsDirty(false)
      return
    }
    if (skillsMenu.status !== "ready") return
    const selection = skillsSelection ?? {}
    const mode = selection.mode === "allowlist" ? "allowlist" : "blocklist"
    setSkillsMode(mode)
    const baseItems = mode === "allowlist" ? selection.allowlist ?? [] : selection.blocklist ?? []
    const normalized = baseItems.filter((item: any) => typeof item === "string") as string[]
    setSkillsSelected(new Set(normalized))
    safeSetSkillsSearch("")
    setSkillsIndex(0)
    setSkillsOffset(0)
    setSkillsDirty(false)
  }, [
    skillsMenu.status,
    skillsSelection,
    setSkillsDirty,
    setSkillsIndex,
    setSkillsMode,
    setSkillsOffset,
    safeSetSkillsSearch,
    setSkillsSelected,
  ])

  useEffect(() => {
    if (modelMenu.status !== "ready") return
    if (modelSearch.trim().length > 0) return
    const currentIndex = filteredModels.findIndex((item) => item.isCurrent)
    if (currentIndex < 0) return
    setModelIndex((prev: number) => (prev === currentIndex ? prev : currentIndex))
    setModelOffset((prev: number) => {
      if (currentIndex < prev) return currentIndex
      if (currentIndex >= prev + MODEL_VISIBLE_ROWS) {
        return Math.max(0, currentIndex - MODEL_VISIBLE_ROWS + 1)
      }
      return prev
    })
  }, [filteredModels, modelMenu.status, modelSearch, MODEL_VISIBLE_ROWS, setModelIndex, setModelOffset])

  useEffect(() => {
    if (!permissionRequest) return
    setPermissionScope(ALWAYS_ALLOW_SCOPE)
    setPermissionScroll(0)
    setPermissionFileIndex(0)
    setPermissionNote("")
    setPermissionNoteCursor(0)
    const initialTab = permissionRequest.diffText ? "diff" : "summary"
    permissionTabRef.current = initialTab
    setPermissionTab(initialTab)
  }, [permissionRequest, setPermissionFileIndex, setPermissionNote, setPermissionNoteCursor, setPermissionScope, setPermissionScroll, setPermissionTab, permissionTabRef])

  useEffect(() => {
    permissionTabRef.current = permissionTab
  }, [permissionTab, permissionTabRef])

  useEffect(() => {
    permissionNoteRef.current = permissionNote
  }, [permissionNote, permissionNoteRef])

  useEffect(() => {
    if (!tasksOpen) return
    setTaskIndex(0)
    setTaskScroll(0)
    setTaskNotice(null)
    setTaskSearchQuery("")
    setTaskStatusFilter("all")
    setTaskTailLines([])
    setTaskTailPath(null)
    setTaskFocusLaneId(null)
  }, [tasksOpen, setTaskFocusLaneId, setTaskIndex, setTaskNotice, setTaskScroll, setTaskSearchQuery, setTaskStatusFilter, setTaskTailLines, setTaskTailPath])

  useEffect(() => {
    if (!tasksOpen) return
    setTaskIndex((prev: number) => Math.max(0, Math.min(prev, Math.max(0, taskRows.length - 1))))
    setTaskScroll((prev: number) => Math.max(0, Math.min(prev, taskMaxScroll)))
  }, [taskMaxScroll, taskRows.length, tasksOpen, setTaskIndex, setTaskScroll])

  useEffect(() => {
    if (!ctreeOpen) return
    setCtreeIndex(0)
    setCtreeScroll(0)
    setCtreeShowDetails(false)
    setCtreeCollapsedNodes(new Set())
    void onCtreeRequest()
  }, [ctreeOpen, onCtreeRequest, setCtreeCollapsedNodes, setCtreeIndex, setCtreeScroll, setCtreeShowDetails, sessionId])

  useEffect(() => {
    if (!ctreeOpen) return
    setCtreeIndex((prev: number) => Math.max(0, Math.min(prev, Math.max(0, ctreeRows.length - 1))))
    setCtreeScroll((prev: number) => Math.max(0, Math.min(prev, ctreeMaxScroll)))
  }, [ctreeMaxScroll, ctreeOpen, ctreeRows.length, setCtreeIndex, setCtreeScroll])

  useEffect(() => {
    return () => {
      if (permissionDecisionTimerRef.current) {
        clearTimeout(permissionDecisionTimerRef.current)
      }
    }
  }, [permissionDecisionTimerRef])

  useEffect(() => {
    if (!todosOpen) return
    setTodoScroll(0)
  }, [todosOpen, todos.length, setTodoScroll])

  useEffect(() => {
    if (rewindMenu.status === "hidden") {
      setRewindIndex(0)
    }
  }, [rewindMenu.status, setRewindIndex])

  useEffect(() => {
    if (!input.startsWith("/")) return
    setSuggestIndex(0)
  }, [input, suggestions.length, setSuggestIndex])

  useEffect(() => {
    if (!escPrimedAt) return
    const timer = setTimeout(() => setEscPrimedAt(null), 700)
    return () => clearTimeout(timer)
  }, [escPrimedAt, setEscPrimedAt])

  useEffect(() => {
    if (!ctrlCPrimedAt) return
    const timer = setTimeout(() => setCtrlCPrimedAt(null), DOUBLE_CTRL_C_WINDOW_MS)
    return () => clearTimeout(timer)
  }, [ctrlCPrimedAt, setCtrlCPrimedAt, DOUBLE_CTRL_C_WINDOW_MS])

  useEffect(() => {
    if (!stdout?.isTTY) return
    if (!transcriptViewerOpen) return
    return () => {
      try {
        stdout.write("[?1006l")
        stdout.write("[?1000l")
        stdout.write("[?1049l")
      } catch {
        // ignore
      }
    }
  }, [stdout, transcriptViewerOpen])

  const enterTranscriptViewer = useCallback(() => {
    if (transcriptViewerOpen) return
    setCtrlCPrimedAt(null)
    setEscPrimedAt(null)
    setTranscriptViewerFollowTail(true)
    setTranscriptViewerScroll(0)
    setTranscriptSearchOpen(false)
    setTranscriptSearchQuery("")
    setTranscriptSearchIndex(0)
    if (stdout?.isTTY) {
      try {
        stdout.write("[?1049h")
        stdout.write("[H[2J")
        stdout.write("[?1000h")
        stdout.write("[?1006h")
      } catch {
        // ignore
      }
    }
    setTranscriptViewerOpen(true)
    setTranscriptExportNotice(null)
  }, [
    setCtrlCPrimedAt,
    setEscPrimedAt,
    setTranscriptExportNotice,
    setTranscriptSearchIndex,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptViewerFollowTail,
    setTranscriptViewerOpen,
    setTranscriptViewerScroll,
    stdout,
    transcriptViewerOpen,
  ])

  const exitTranscriptViewer = useCallback(() => {
    if (!transcriptViewerOpen) return
    if (stdout?.isTTY) {
      try {
        stdout.write("[?1006l")
        stdout.write("[?1000l")
        stdout.write("[?1049l")
      } catch {
        // ignore
      }
    }
    setTranscriptViewerOpen(false)
    setTranscriptViewerFollowTail(true)
    setTranscriptViewerScroll(0)
    setTranscriptSearchOpen(false)
    setTranscriptSearchQuery("")
    setTranscriptSearchIndex(0)
    setTranscriptExportNotice(null)
    setEscPrimedAt(null)
  }, [
    setEscPrimedAt,
    setTranscriptExportNotice,
    setTranscriptSearchIndex,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptViewerFollowTail,
    setTranscriptViewerOpen,
    setTranscriptViewerScroll,
    stdout,
    transcriptViewerOpen,
  ])

  const saveTranscriptExport = useCallback(async () => {
    try {
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-")
      const dirPath = path.join(process.cwd(), "artifacts", "transcripts")
      await fs.mkdir(dirPath, { recursive: true })
      const filePath = path.join(dirPath, `transcript-${timestamp}.txt`)
      await fs.writeFile(filePath, transcriptViewerLines.join("\n"), "utf8")
      setTranscriptExportNotice(`Saved to ${filePath}`)
      pushCommandResult("Transcript saved", [filePath])
    } catch (error) {
      const message = (error as Error).message || String(error)
      pushCommandResult("Transcript export failed", [message])
    }
  }, [pushCommandResult, transcriptViewerLines, setTranscriptExportNotice])

  return {
    skillsCatalog,
    skillsSelection,
    skillsSources,
    skillsData,
    skillsSelectedEntry,
    skillsDisplayRows,
    skillsDisplayIndex,
    resetSkillsSelection,
    toggleSkillsMode,
    toggleSkillSelection,
    applySkillsSelection,
    filteredModels,
    modelProviderOrder,
    modelProviderLabel,
    modelProviderCounts,
    enterTranscriptViewer,
    exitTranscriptViewer,
    saveTranscriptExport,
  }
}
