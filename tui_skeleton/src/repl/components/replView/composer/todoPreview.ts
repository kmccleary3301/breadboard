import type { TodoPreviewSelectionStrategy, TodoStoreSnapshot } from "../../../types.js"
import { selectTodoPreviewItems, todoStoreCounts } from "../../../todos/todoStore.js"

export type TodoPreviewItem = {
  readonly id: string
  readonly label: string
  readonly status: string
}

export type TodoPreviewModel = {
  readonly header: string
  readonly doneCount: number
  readonly totalCount: number
  readonly items: readonly TodoPreviewItem[]
}

export const DEFAULT_TODO_PREVIEW_MAX_ITEMS = 7

type BuildTodoPreviewOptions = {
  readonly maxItems?: number
  readonly strategy?: TodoPreviewSelectionStrategy
  readonly showHeader?: boolean
  readonly showHiddenCount?: boolean
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

export const buildTodoPreviewModel = (
  store: TodoStoreSnapshot,
  options: BuildTodoPreviewOptions = {},
): TodoPreviewModel | null => {
  if (!store || store.order.length === 0) return null
  const maxItems = options.maxItems ?? DEFAULT_TODO_PREVIEW_MAX_ITEMS
  const strategy = options.strategy ?? "first_n"
  const showHeader = options.showHeader ?? true
  const showHiddenCount = options.showHiddenCount ?? false

  const { done: doneCount, total: totalCount } = todoStoreCounts(store)
  const selection = selectTodoPreviewItems(store, { maxItems, strategy })
  const items: TodoPreviewItem[] = selection.visible.map((todo) => ({
    id: todo.id,
    status: todo.status,
    label: `[${statusMark(todo.status)}] ${todo.title}`,
  }))

  const hiddenSuffix = showHiddenCount && selection.hiddenCount > 0 ? ` · +${selection.hiddenCount}` : ""
  const header = showHeader ? `TODOs: ${doneCount}/${totalCount}${hiddenSuffix}` : ""
  return { header, doneCount, totalCount, items }
}

export const getTodoPreviewRowCount = (model: TodoPreviewModel | null): number => {
  if (!model || model.items.length === 0) return 0
  const headerRows = model.header ? 1 : 0
  return headerRows + model.items.length
}
