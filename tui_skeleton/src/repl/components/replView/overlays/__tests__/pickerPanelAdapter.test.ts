import { describe, expect, it } from "vitest"
import { buildPickerWindow, resolvePickerLayout, type PickerModel } from "../../../../pickerModel.js"
import { buildPickerPanelProjection } from "../pickerPanelAdapter.js"

const model: PickerModel = {
  id: "models",
  title: "Models",
  query: "gpt",
  index: 2,
  detailPane: true,
  rows: [
    { id: "header", kind: "header", label: "OpenAI" },
    { id: "mini", label: "gpt-5.4-mini", description: "fast", tags: ["codex"], current: true },
    { id: "pro", label: "gpt-5.5-pro", description: "planner", detail: "Large planning model", default: true },
    { id: "blocked", label: "future", disabled: true, disabledReason: "not configured" },
  ],
}

describe("pickerPanelAdapter", () => {
  it("projects picker rows into SelectPanel rows with active/current/default markers", () => {
    const layout = resolvePickerLayout({ width: 120, rows: 36 }, { detailPane: true })
    const window = buildPickerWindow(model, layout)
    const projection = buildPickerPanelProjection({
      model,
      window,
      layout,
      activeColor: "black",
      activeBackground: "cyan",
    })
    expect(projection.titleLines).toEqual([{ text: "Models", color: "green" }])
    expect(projection.rows.map((row) => row.text)).toEqual([
      "OpenAI",
      "  ● gpt-5.4-mini — fast [codex]",
      "› ★ gpt-5.5-pro — planner · Large planning model",
    ])
    expect(projection.rows[2]).toMatchObject({ isActive: true, activeBackground: "cyan" })
    expect(projection.footerLines).toContainEqual({ text: "Large planning model", color: "gray" })
  })

  it("uses compact disabled-row text on narrow layouts", () => {
    const compactModel: PickerModel = { ...model, query: "future", index: 1 }
    const layout = resolvePickerLayout({ width: 64, rows: 16 }, { detailPane: true })
    const window = buildPickerWindow(compactModel, layout)
    const projection = buildPickerPanelProjection({
      model: compactModel,
      window,
      layout,
      activeColor: "black",
      activeBackground: "cyan",
    })
    expect(projection.rows.map((row) => row.text)).toEqual(["OpenAI", "› × future — not configured"])
    expect(projection.rows[1]).toMatchObject({ color: "dim" })
  })
})
