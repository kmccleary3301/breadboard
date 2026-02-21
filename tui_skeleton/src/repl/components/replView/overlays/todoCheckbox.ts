import { ASCII_ONLY } from "../theme.js"

const normalizeStatus = (status?: string): string => String(status ?? "").trim().toLowerCase()

export const todoCheckboxToken = (status?: string): string => {
  const normalized = normalizeStatus(status)
  if (normalized === "done" || normalized === "complete" || normalized === "completed") {
    return ASCII_ONLY ? "[x]" : "🗹"
  }
  if (normalized === "blocked" || normalized === "failed" || normalized === "canceled" || normalized === "cancelled") {
    return ASCII_ONLY ? "[!]" : "☒"
  }
  return ASCII_ONLY ? "[ ]" : "☐"
}

export const formatTodoModalRowLabel = (label: string, status?: string): string => `  ${todoCheckboxToken(status)} ${label}`
