import type { TodoPreviewSelectionStrategy, TodoStoreSnapshot } from "../../../types.js"
import { selectTodoPreviewItems, todoStoreCounts } from "../../../todos/todoStore.js"

export type TodoPreviewItem = {
  readonly id: string
  readonly label: string
  readonly status: string
}

export type TodoPreviewModel = {
  readonly header: string
  readonly headerLine: string
  readonly doneCount: number
  readonly totalCount: number
  readonly items: readonly TodoPreviewItem[]
  readonly style: "minimal" | "nice" | "dense"
  readonly hint: string | null
  readonly frameWidth: number | null
}

export const DEFAULT_TODO_PREVIEW_MAX_ITEMS = 7

type BuildTodoPreviewOptions = {
  readonly maxItems?: number
  readonly strategy?: TodoPreviewSelectionStrategy
  readonly showHeader?: boolean
  readonly showHiddenCount?: boolean
  readonly scopeKey?: string | null
  readonly scopeLabel?: string | null
  readonly stale?: boolean
  readonly style?: "minimal" | "nice" | "dense"
  readonly cols?: number | null
}

const extractCtreeNodeId = (metadata: Record<string, unknown> | null | undefined): string | null => {
  if (!metadata) return null
  const raw =
    typeof metadata.ctree_node_id === "string"
      ? metadata.ctree_node_id
      : typeof metadata.ctreeNodeId === "string"
        ? metadata.ctreeNodeId
        : typeof metadata.node_id === "string"
          ? metadata.node_id
          : typeof metadata.nodeId === "string"
            ? metadata.nodeId
            : null
  const cleaned = raw?.trim() || ""
  return cleaned ? cleaned : null
}

const statusMark = (status: string): string => {
  switch (status) {
    case "done":
      return "x"
    case "in_progress":
      return "~"
    case "blocked":
      return "!"
    case "canceled":
      return "-"
    default:
      return " "
  }
}

const formatHeaderWithHint = (header: string, hint: string, cols: number | null): string => {
  const safeCols = cols != null && Number.isFinite(cols) ? Math.max(10, Math.floor(cols) - 2) : null
  const suffix = `· ${hint}`
  if (!safeCols) return `${header} ${suffix}`
  const headerLen = header.length
  const suffixLen = suffix.length
  const minSpacer = 1
  const remaining = safeCols - headerLen - suffixLen
  if (remaining >= minSpacer) {
    return `${header}${" ".repeat(Math.max(minSpacer, remaining))}${suffix}`
  }
  return `${header} ${suffix}`
}

export const buildTodoPreviewModel = (
  store: TodoStoreSnapshot,
  options: BuildTodoPreviewOptions = {},
): TodoPreviewModel | null => {
  if (!store || store.order.length === 0) return null
  const maxItems = options.maxItems ?? DEFAULT_TODO_PREVIEW_MAX_ITEMS
  const strategy = options.strategy ?? "first_n"
  const showHeader = options.showHeader ?? true
  const showHiddenCount = options.showHiddenCount ?? false
  const scopeKey = options.scopeKey?.trim() || null
  const scopeLabel = options.scopeLabel?.trim() || null
  const stale = Boolean(options.stale)
  const style = options.style ?? "minimal"
  const cols = options.cols ?? null

  const { done: doneCount, total: totalCount } = todoStoreCounts(store)
  const selection = selectTodoPreviewItems(store, { maxItems, strategy })
  const items: TodoPreviewItem[] = selection.visible.map((todo) => ({
    id: todo.id,
    status: todo.status,
    label: `[${statusMark(todo.status)}] ${todo.title}`,
  }))
  const anyCtreeLink = selection.visible.some((todo) => extractCtreeNodeId(todo.metadata ?? null) != null)

  const hiddenSuffix = showHiddenCount && selection.hiddenCount > 0 ? ` · +${selection.hiddenCount}` : ""
  const scopeName = scopeLabel ?? scopeKey
  const showScope = Boolean(scopeName && scopeName !== "main")
  const scopePrefix = showScope ? ` (${scopeName})` : ""
  const treeSuffix = style === "dense" && anyCtreeLink ? " · tree" : ""
  const staleSuffix = stale && style === "dense" ? " (stale)" : ""
  const header = showHeader
    ? `TODOs${scopePrefix}: ${doneCount}/${totalCount}${hiddenSuffix}${treeSuffix}${staleSuffix}`
    : ""
  const hint = style !== "minimal" && cols != null && cols >= 28 ? "Ctrl+T" : null
  const headerLine = header && hint ? formatHeaderWithHint(header, hint, cols) : header
  const frameWidth =
    style === "minimal" || cols == null || !Number.isFinite(cols) ? null : Math.max(10, Math.floor(cols) - 2)
  return { header, headerLine, doneCount, totalCount, items, style, hint, frameWidth }
}

export const getTodoPreviewRowCount = (model: TodoPreviewModel | null): number => {
  if (!model || model.items.length === 0) return 0
  const headerRows = model.header ? 1 : 0
  return headerRows + model.items.length
}
