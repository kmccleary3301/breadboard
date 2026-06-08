import { describe, expect, it } from "vitest"
import {
  buildPickerWindow,
  clampPickerIndex,
  filterPickerRows,
  movePickerIndex,
  resolvePickerConfirm,
  resolvePickerLayout,
  type PickerRow,
} from "../pickerModel.js"

const rows: PickerRow[] = [
  { id: "models", kind: "header", label: "Models" },
  { id: "gpt-5.4-mini", label: "gpt-5.4-mini", description: "Fast Codex lane", tags: ["codex", "default"], current: true },
  { id: "gpt-5.5-pro", label: "gpt-5.5-pro", description: "Planner", tags: ["planner"] },
  { id: "disabled", label: "future model", disabled: true, disabledReason: "Provider unavailable." },
]

describe("pickerModel", () => {
  it("filters rows by label, description, detail, and tags while retaining matching section headers", () => {
    expect(filterPickerRows(rows, "codex").map((row) => row.id)).toEqual(["models", "gpt-5.4-mini"])
    expect(filterPickerRows(rows, "missing")).toEqual([{ id: "empty", kind: "empty", label: "No matches." }])
  })

  it("clamps and moves selection across selectable rows only", () => {
    expect(clampPickerIndex(rows, 0)).toBe(1)
    expect(movePickerIndex(rows, 1, 1)).toBe(2)
    expect(movePickerIndex(rows, 2, 1)).toBe(1)
    expect(movePickerIndex(rows, 1, -1)).toBe(2)
  })

  it("resolves compact and detail-pane layout from viewport constraints", () => {
    expect(resolvePickerLayout({ width: 64, rows: 16 }, { detailPane: true })).toMatchObject({
      compact: true,
      showDetailPane: false,
      showTags: false,
    })
    expect(resolvePickerLayout({ width: 120, rows: 36 }, { detailPane: true })).toMatchObject({
      compact: false,
      showDetailPane: true,
      showTags: true,
    })
  })

  it("builds a snapshot-friendly visible picker window", () => {
    const layout = resolvePickerLayout({ width: 80, rows: 18 })
    const window = buildPickerWindow({ id: "models", title: "Models", rows, query: "gpt", index: 2 }, layout)
    expect(window.activeRow?.id).toBe("gpt-5.5-pro")
    expect(window.rows.map((row) => row.id)).toEqual(["models", "gpt-5.4-mini", "gpt-5.5-pro"])
  })

  it("returns disabled confirm reasons instead of pretending every row is selectable", () => {
    expect(resolvePickerConfirm(rows[3])).toEqual({ kind: "disabled", row: rows[3], reason: "Provider unavailable." })
    expect(resolvePickerConfirm(rows[2])).toEqual({ kind: "confirm", row: rows[2] })
    expect(resolvePickerConfirm(rows[0])).toEqual({ kind: "none" })
  })
})
