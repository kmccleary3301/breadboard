export type TodoStatus = "todo" | "in_progress" | "done" | "blocked" | "canceled"

const TODO_STATUS_BY_INPUT: Readonly<Record<string, TodoStatus>> = {
  todo: "todo",
  pending: "todo",
  in_progress: "in_progress",
  progress: "in_progress",
  active: "in_progress",
  done: "done",
  complete: "done",
  completed: "done",
  blocked: "blocked",
  failed: "blocked",
  canceled: "canceled",
  cancelled: "canceled",
}

export const normalizeTodoStatus = (value: unknown): TodoStatus =>
  TODO_STATUS_BY_INPUT[String(value ?? "").trim().toLowerCase()] ?? "todo"
