import { todoStatusPresentation } from "../../../todos/todoStatusPresentation.js"
import { ASCII_ONLY } from "../theme.js"

export const todoCheckboxToken = (status?: string): string => {
  const symbol = todoStatusPresentation(status).previewSymbol
  return ASCII_ONLY ? symbol.ascii : symbol.unicode
}

export const formatTodoModalRowLabel = (label: string, status?: string): string => `  ${todoCheckboxToken(status)} ${label}`
