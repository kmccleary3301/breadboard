import type { SelectPanelLine, SelectPanelRow } from "../../SelectPanel.js"
import type { PickerLayout, PickerModel, PickerRow, PickerWindow } from "../../../pickerModel.js"

export interface PickerPanelProjection {
  readonly titleLines: readonly SelectPanelLine[]
  readonly hintLines: readonly SelectPanelLine[]
  readonly rows: readonly SelectPanelRow[]
  readonly footerLines: readonly SelectPanelLine[]
}

const markerForRow = (row: PickerRow): string => {
  if (row.disabled) return "×"
  if (row.current) return "●"
  if (row.default) return "★"
  if (row.selected) return "✓"
  return " "
}

const compactText = (row: PickerRow, active: boolean): string => {
  const prefix = active ? "›" : " "
  const marker = markerForRow(row)
  const reason = row.disabled && row.disabledReason ? ` — ${row.disabledReason}` : ""
  return `${prefix} ${marker} ${row.label}${reason}`
}

const fullText = (row: PickerRow, active: boolean, layout: PickerLayout): string => {
  const prefix = active ? "›" : " "
  const marker = markerForRow(row)
  const meta = [row.description, row.detail].filter(Boolean).join(" · ")
  const tags = layout.showTags && row.tags?.length ? ` [${row.tags.join(", ")}]` : ""
  const reason = row.disabled && row.disabledReason ? ` — ${row.disabledReason}` : ""
  return `${prefix} ${marker} ${row.label}${meta ? ` — ${meta}` : ""}${tags}${reason}`
}

export const pickerRowToSelectPanelRow = (input: {
  readonly row: PickerRow
  readonly active: boolean
  readonly layout: PickerLayout
  readonly activeColor: string
  readonly activeBackground: string
}): SelectPanelRow => {
  const { row, active, layout, activeColor, activeBackground } = input
  const kind = row.kind ?? "item"
  if (kind === "header") return { kind: "header", text: row.label, color: "dim" }
  if (kind === "empty") return { kind: "empty", text: row.label, color: "dim" }
  return {
    kind: "item",
    text: layout.compact ? compactText(row, active) : fullText(row, active, layout),
    isActive: active,
    activeColor,
    activeBackground,
    color: row.disabled ? "dim" : undefined,
  }
}

export const buildPickerPanelProjection = (input: {
  readonly model: PickerModel
  readonly window: PickerWindow
  readonly layout: PickerLayout
  readonly activeColor: string
  readonly activeBackground: string
  readonly titleColor?: string
}): PickerPanelProjection => {
  const { model, window, layout, activeColor, activeBackground, titleColor = "green" } = input
  const query = model.query?.trim() ?? ""
  const titleLines: SelectPanelLine[] = [{ text: model.title, color: titleColor }]
  const hintLines: SelectPanelLine[] = [
    {
      text: `${query.length > 0 ? `Search: ${query}` : "Type to filter"} · ↑/↓ select · Enter confirm · Esc cancel`,
      color: "dim",
    },
  ]
  const rows = window.rows.map((row) =>
    pickerRowToSelectPanelRow({
      row,
      active: row.id === window.activeRow?.id,
      layout,
      activeColor,
      activeBackground,
    }),
  )
  const footerLines: SelectPanelLine[] = []
  if (window.total > layout.visibleRows) {
    footerLines.push({
      text: `${window.offset + 1}-${Math.min(window.offset + layout.visibleRows, window.total)} of ${window.total}`,
      color: "dim",
    })
  }
  if (layout.showDetailPane && window.activeRow?.detail) {
    footerLines.push({ text: window.activeRow.detail, color: "gray" })
  }
  return { titleLines, hintLines, rows, footerLines }
}
