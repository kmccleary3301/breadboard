import { NEUTRAL_COLORS, SEMANTIC_COLORS } from "../designSystem.js"
import { normalizeTodoStatus, type TodoStatus } from "./todoStatus.js"

export type TodoPresentationStatus = TodoStatus

export interface TodoStatusPresentation {
  readonly label: string
  readonly previewSymbol: {
    readonly ascii: string
    readonly unicode: string
  }
  readonly textMark: string
  readonly previewColor: string
  readonly panelColor: string
}

const OPEN_TODO_PRESENTATION: TodoStatusPresentation = {
  label: "Todo",
  previewSymbol: { ascii: "[ ]", unicode: "☐" },
  textMark: " ",
  previewColor: NEUTRAL_COLORS.nearWhite,
  panelColor: SEMANTIC_COLORS.warning,
}

const TODO_STATUS_PRESENTATION: Record<TodoPresentationStatus, TodoStatusPresentation> = {
  todo: OPEN_TODO_PRESENTATION,
  in_progress: {
    label: "In Progress",
    previewSymbol: { ascii: "[ ]", unicode: "☐" },
    textMark: "~",
    previewColor: SEMANTIC_COLORS.warning,
    panelColor: SEMANTIC_COLORS.info,
  },
  done: {
    label: "Done",
    previewSymbol: { ascii: "[x]", unicode: "🗹" },
    textMark: "x",
    previewColor: "dim",
    panelColor: SEMANTIC_COLORS.success,
  },
  blocked: {
    label: "Blocked",
    previewSymbol: { ascii: "[!]", unicode: "☒" },
    textMark: "!",
    previewColor: SEMANTIC_COLORS.error,
    panelColor: SEMANTIC_COLORS.error,
  },
  canceled: {
    label: "Canceled",
    previewSymbol: { ascii: "[!]", unicode: "☒" },
    textMark: "-",
    previewColor: NEUTRAL_COLORS.dimGray,
    panelColor: NEUTRAL_COLORS.midGray,
  },
}


export const todoStatusPresentation = (status: string | null | undefined): TodoStatusPresentation =>
  TODO_STATUS_PRESENTATION[normalizeTodoStatus(status)]
