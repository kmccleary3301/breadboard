export type PickerRowKind = "item" | "header" | "empty"
export type PickerActionKind = "none" | "confirm" | "cancel" | "move" | "page" | "search" | "toggle" | "back"
export type PickerSelectionMode = "single" | "multi" | "read-only"

export interface PickerRow {
  readonly id: string
  readonly kind?: PickerRowKind
  readonly label: string
  readonly description?: string
  readonly detail?: string
  readonly tags?: readonly string[]
  readonly current?: boolean
  readonly default?: boolean
  readonly selected?: boolean
  readonly disabled?: boolean
  readonly disabledReason?: string
}

export interface PickerModel {
  readonly id: string
  readonly title: string
  readonly rows: readonly PickerRow[]
  readonly query?: string
  readonly index?: number
  readonly selectionMode?: PickerSelectionMode
  readonly detailPane?: boolean
}

export interface PickerViewport {
  readonly width: number
  readonly rows: number
}

export interface PickerLayout {
  readonly width: number
  readonly visibleRows: number
  readonly compact: boolean
  readonly showDetailPane: boolean
  readonly showTags: boolean
}

export interface PickerWindow {
  readonly rows: readonly PickerRow[]
  readonly index: number
  readonly offset: number
  readonly total: number
  readonly activeRow: PickerRow | null
}

const normalize = (value: string): string => value.trim().toLowerCase()

const rowSearchText = (row: PickerRow): string =>
  [row.label, row.description, row.detail, ...(row.tags ?? [])].filter(Boolean).join(" ").toLowerCase()

export const isSelectablePickerRow = (row: PickerRow | null | undefined): row is PickerRow =>
  Boolean(row && (row.kind ?? "item") === "item" && row.disabled !== true)

export const filterPickerRows = (rows: readonly PickerRow[], query = ""): PickerRow[] => {
  const needle = normalize(query)
  if (!needle) return [...rows]
  const filtered: PickerRow[] = []
  let pendingHeaders: PickerRow[] = []
  for (const row of rows) {
    const kind = row.kind ?? "item"
    if (kind === "header") {
      pendingHeaders.push(row)
      continue
    }
    if (kind === "empty") continue
    if (!rowSearchText(row).includes(needle)) continue
    filtered.push(...pendingHeaders)
    pendingHeaders = []
    filtered.push(row)
  }
  return filtered.length > 0 ? filtered : [{ id: "empty", kind: "empty", label: "No matches." }]
}

export const clampPickerIndex = (rows: readonly PickerRow[], index = 0): number => {
  if (rows.length === 0) return 0
  const clamped = Math.max(0, Math.min(Math.floor(index), rows.length - 1))
  if (isSelectablePickerRow(rows[clamped])) return clamped
  const forward = rows.findIndex((row, rowIndex) => rowIndex >= clamped && isSelectablePickerRow(row))
  if (forward >= 0) return forward
  for (let rowIndex = clamped - 1; rowIndex >= 0; rowIndex -= 1) {
    if (isSelectablePickerRow(rows[rowIndex])) return rowIndex
  }
  return clamped
}

export const movePickerIndex = (rows: readonly PickerRow[], index: number, delta: number): number => {
  if (rows.length === 0) return 0
  const direction = delta < 0 ? -1 : 1
  let next = clampPickerIndex(rows, index)
  const attempts = rows.length
  for (let count = 0; count < attempts; count += 1) {
    next = (next + direction + rows.length) % rows.length
    if (isSelectablePickerRow(rows[next])) return next
  }
  return clampPickerIndex(rows, index)
}

export const resolvePickerLayout = (viewport: PickerViewport, options: { readonly detailPane?: boolean } = {}): PickerLayout => {
  const width = Math.max(40, Math.floor(viewport.width))
  const rows = Math.max(6, Math.floor(viewport.rows))
  const compact = width < 80 || rows < 18
  const visibleRows = Math.max(3, Math.min(compact ? 6 : 12, rows - (compact ? 6 : 10)))
  return {
    width,
    visibleRows,
    compact,
    showDetailPane: options.detailPane === true && !compact && width >= 112,
    showTags: !compact && width >= 90,
  }
}

export const buildPickerWindow = (model: PickerModel, layout: PickerLayout): PickerWindow => {
  const rows = filterPickerRows(model.rows, model.query)
  const index = clampPickerIndex(rows, model.index ?? 0)
  const maxOffset = Math.max(0, rows.length - layout.visibleRows)
  const half = Math.floor(layout.visibleRows / 2)
  const offset = Math.max(0, Math.min(maxOffset, index - half))
  return {
    rows: rows.slice(offset, offset + layout.visibleRows),
    index,
    offset,
    total: rows.length,
    activeRow: rows[index] ?? null,
  }
}

export const resolvePickerConfirm = (row: PickerRow | null | undefined):
  | { readonly kind: "confirm"; readonly row: PickerRow }
  | { readonly kind: "disabled"; readonly row: PickerRow; readonly reason: string }
  | { readonly kind: "none" } => {
  if (!row || (row.kind ?? "item") !== "item") return { kind: "none" }
  if (row.disabled) return { kind: "disabled", row, reason: row.disabledReason ?? "This option is unavailable." }
  return { kind: "confirm", row }
}
