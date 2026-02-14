import type { TodoItem } from "../../../types.js"

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
  readonly showHeader?: boolean
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
  todos: readonly TodoItem[],
  options: BuildTodoPreviewOptions = {},
): TodoPreviewModel | null => {
  if (!todos || todos.length === 0) return null
  const maxItems = options.maxItems ?? DEFAULT_TODO_PREVIEW_MAX_ITEMS
  const showHeader = options.showHeader ?? true

  const totalCount = todos.length
  let doneCount = 0
  for (const todo of todos) {
    if (todo.status === "done") doneCount += 1
  }

  const visible = todos.slice(0, Math.max(0, maxItems))
  const items: TodoPreviewItem[] = visible.map((todo) => ({
    id: todo.id,
    status: todo.status,
    label: `[${statusMark(todo.status)}] ${todo.title}`,
  }))

  const header = showHeader ? `TODOs: ${doneCount}/${totalCount}` : ""
  return { header, doneCount, totalCount, items }
}

export const getTodoPreviewRowCount = (model: TodoPreviewModel | null): number => {
  if (!model || model.items.length === 0) return 0
  const headerRows = model.header ? 1 : 0
  return headerRows + model.items.length
}

