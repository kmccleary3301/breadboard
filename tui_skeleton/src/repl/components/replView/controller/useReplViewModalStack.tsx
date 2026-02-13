import type React from "react"
import { buildModalStack } from "../overlays/buildModalStack.js"

type ModalStackContext = {
  confirmState: unknown
  shortcutsOpen: boolean
  claudeChrome: boolean
  isBreadboardProfile: boolean
  columnWidth: number
  contentWidth: number
  sessionId: string
  status: string
  mode: string | null
  pendingResponse: boolean
  stats: unknown
  paletteState: unknown
  paletteItems: unknown
  shortcutLines: React.ReactNode[]
  clearToEnd: (...args: any[]) => string
  modelMenu: unknown
  modelMenuCompact: boolean
  modelSearch: string
  modelProviderLabel: string
  modelProviderFilter: string
  modelMenuHeaderText: string
  filteredModels: unknown[]
  visibleModelRows: unknown[]
  modelIndex: number
  modelOffset: number
  formatModelRowText: (row: unknown) => string
  skillsMenu: unknown
  skillsSelected: unknown
  skillsMode: unknown
  skillsSearch: string
  skillsIndex: number
  skillsOffset: number
  skillsSources: unknown
  skillsDirty: boolean
  rewindMenu: unknown
  todosOpen: boolean
  todoScroll: number
  todos: unknown[]
  usageOpen: boolean
  inspectMenu: unknown
  inspectRawOpen: boolean
  inspectRawScroll: number
  tasks: unknown[]
  tasksOpen: boolean
  taskFocusViewOpen: boolean
  taskFocusFollowTail: boolean
  taskFocusRawMode: boolean
  taskFocusTailLines: number
  taskFocusMode: "lane" | "swap"
  taskFocusLaneId: string | null
  taskFocusLaneLabel: string | null
  taskScroll: number
  taskSearchQuery: string
  taskStatusFilter: string
  taskLaneFilter: string
  taskGroupMode: string
  taskCollapsedGroupKeys: Set<string>
  permissionRequest: unknown
  permissionQueueDepth: number
  permissionTab: unknown
  permissionScope: unknown
  permissionScroll: number
  permissionError: string | null
  permissionNote: string
  permissionNoteCursor: number
  renderPermissionNoteLine: (value: string, index: number) => React.ReactNode
  ctreeOpen: boolean
  ctreeScroll: number
  ctreeStage: string | null
  ctreeIncludePreviews: boolean
  ctreeSource: string | null
  ctreeTree: unknown
  ctreeTreeStatus: string | null
  ctreeTreeError: string | null
  ctreeUpdatedAt: number | null
  ctreeCollapsedNodes: Set<string>
  ctreeShowDetails: boolean
  formatCtreeSummary: (value?: unknown | null) => string | null
  ctreeSnapshot: unknown
  PANEL_WIDTH: number
  MAX_VISIBLE_MODELS: number
  MODEL_VISIBLE_ROWS: number
  SKILLS_VISIBLE_ROWS: number
  isSkillSelected: (selected: Set<string>, skill: unknown) => boolean
  panels: Record<string, any>
  menus: Record<string, any>
}

export const useReplViewModalStack = (context: ModalStackContext) => {
  const {
    confirmState,
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    contentWidth,
    sessionId,
    status,
    mode,
    pendingResponse,
    stats,
    paletteState,
    paletteItems,
    shortcutLines,
    clearToEnd,
    modelMenu,
    modelMenuCompact,
    modelSearch,
    modelProviderLabel,
    modelProviderFilter,
    modelMenuHeaderText,
    filteredModels,
    visibleModelRows,
    modelIndex,
    modelOffset,
    formatModelRowText,
    skillsMenu,
    skillsSelected,
    skillsMode,
    skillsSearch,
    skillsIndex,
    skillsOffset,
    skillsSources,
    skillsDirty,
    rewindMenu,
    todosOpen,
    todoScroll,
    todos,
    usageOpen,
    inspectMenu,
    inspectRawOpen,
    inspectRawScroll,
    tasks,
    tasksOpen,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    taskFocusMode,
    taskFocusLaneId,
    taskFocusLaneLabel,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskGroupMode,
    taskCollapsedGroupKeys,
    permissionRequest,
    permissionQueueDepth,
    permissionTab,
    permissionScope,
    permissionScroll,
    permissionError,
    permissionNote,
    permissionNoteCursor,
    renderPermissionNoteLine,
    ctreeOpen,
    ctreeScroll,
    ctreeStage,
    ctreeIncludePreviews,
    ctreeSource,
    ctreeTree,
    ctreeTreeStatus,
    ctreeTreeError,
    ctreeUpdatedAt,
    ctreeCollapsedNodes,
    ctreeShowDetails,
    formatCtreeSummary,
    ctreeSnapshot,
    PANEL_WIDTH,
    MAX_VISIBLE_MODELS,
    MODEL_VISIBLE_ROWS,
    SKILLS_VISIBLE_ROWS,
    isSkillSelected,
    panels,
    menus,
  } = context

  const {
    inspectRawLines,
    inspectRawMaxScroll,
    inspectRawViewportRows,
    rewindCheckpoints,
    rewindSelectedIndex,
    rewindVisibleLimit,
    rewindOffset,
    rewindVisible,
    todoRows,
    todoViewportRows,
    todoMaxScroll,
    taskRows,
    diagnosticsHeatmapRows,
    taskGroups,
    taskViewportRows,
    taskMaxScroll,
    selectedTaskIndex,
    selectedTaskRow,
    selectedTask,
    taskNotice,
    taskTailLines,
    taskTailPath,
    ctreeRows,
    ctreeViewportRows,
    ctreeMaxScroll,
    selectedCTreeIndex,
    selectedCTreeRow,
    formatCTreeNodeLabel,
    formatCTreeNodePreview,
    formatCTreeNodeFlags,
    permissionDiffLines,
    permissionViewportRows,
    permissionDiffSections,
    permissionDiffPreview,
    permissionSelectedSection,
    permissionSelectedFileIndex,
  } = panels

  const { skillsDisplayRows } = menus

  return buildModalStack({
    confirmState,
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    PANEL_WIDTH,
    shortcutLines,
    paletteState,
    paletteItems,
    MAX_VISIBLE_MODELS,
    clearToEnd,
    modelMenu,
    modelMenuCompact,
    modelSearch,
    modelProviderLabel,
    modelProviderFilter,
    stats,
    filteredModels,
    modelMenuHeaderText,
    visibleModelRows,
    modelIndex,
    formatModelRowText,
    modelOffset,
    MODEL_VISIBLE_ROWS,
    skillsMenu,
    skillsSelected,
    skillsMode,
    skillsSearch,
    skillsIndex,
    skillsDisplayRows,
    skillsOffset,
    SKILLS_VISIBLE_ROWS,
    skillsSources,
    isSkillSelected,
    skillsDirty,
    rewindMenu,
    rewindCheckpoints,
    rewindVisible,
    rewindOffset,
    rewindSelectedIndex,
    rewindVisibleLimit,
    todosOpen,
    todoScroll,
    todoMaxScroll,
    todoRows,
    todoViewportRows,
    todos,
    usageOpen,
    contentWidth,
    inspectMenu,
    inspectRawOpen,
    inspectRawScroll,
    inspectRawMaxScroll,
    inspectRawViewportRows,
    inspectRawLines,
    sessionId,
    status,
    mode,
    pendingResponse,
    tasks,
    ctreeOpen,
    ctreeScroll,
    ctreeMaxScroll,
    ctreeRows,
    ctreeViewportRows,
    ctreeStage,
    ctreeIncludePreviews,
    ctreeSource,
    ctreeTree,
    ctreeTreeStatus,
    ctreeTreeError,
    ctreeUpdatedAt,
    ctreeCollapsedNodes,
    selectedCTreeIndex,
    selectedCTreeRow,
    ctreeShowDetails,
    formatCTreeNodeLabel,
    formatCTreeNodePreview,
    formatCTreeNodeFlags,
    tasksOpen,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    taskFocusMode,
    taskFocusLaneId,
    taskFocusLaneLabel,
    taskScroll,
    taskMaxScroll,
    taskRows,
    diagnosticsHeatmapRows,
    taskViewportRows,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskGroupMode,
    taskCollapsedGroupKeys,
    selectedTaskIndex,
    selectedTaskRow,
    selectedTask,
    taskGroups,
    taskNotice,
    taskTailLines,
    taskTailPath,
    formatCtreeSummary,
    ctreeSnapshot,
    permissionRequest,
    permissionQueueDepth,
    permissionTab,
    permissionScope,
    permissionDiffLines,
    permissionViewportRows,
    permissionDiffSections,
    permissionDiffPreview,
    permissionSelectedSection,
    permissionSelectedFileIndex,
    permissionScroll,
    permissionError,
    permissionNote,
    permissionNoteCursor,
    renderPermissionNoteLine,
  })
}
