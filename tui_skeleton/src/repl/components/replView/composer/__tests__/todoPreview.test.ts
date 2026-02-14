import { describe, expect, it } from "vitest"
import { buildTodoPreviewModel, DEFAULT_TODO_PREVIEW_MAX_ITEMS, getTodoPreviewRowCount } from "../todoPreview.js"

describe("buildTodoPreviewModel", () => {
  it("returns null for empty input", () => {
    expect(buildTodoPreviewModel([])).toBeNull()
  })

  it("builds header and item labels with progress", () => {
    const model = buildTodoPreviewModel([
      { id: "1", title: "First", status: "todo" },
      { id: "2", title: "Second", status: "done" },
      { id: "3", title: "Third", status: "in_progress" },
    ])
    expect(model?.header).toBe("TODOs: 1/3")
    expect(model?.items.map((item) => item.label)).toEqual(["[ ] First", "[x] Second", "[~] Third"])
    expect(getTodoPreviewRowCount(model)).toBe(1 + 3)
  })

  it("truncates to maxItems and preserves order", () => {
    const todos = Array.from({ length: DEFAULT_TODO_PREVIEW_MAX_ITEMS + 4 }).map((_, idx) => ({
      id: String(idx + 1),
      title: `Item ${idx + 1}`,
      status: idx % 2 === 0 ? "todo" : "done",
    }))
    const model = buildTodoPreviewModel(todos, { maxItems: DEFAULT_TODO_PREVIEW_MAX_ITEMS })
    expect(model?.items).toHaveLength(DEFAULT_TODO_PREVIEW_MAX_ITEMS)
    expect(model?.items[0]?.label).toBe("[ ] Item 1")
    expect(model?.items[DEFAULT_TODO_PREVIEW_MAX_ITEMS - 1]?.label).toBe(`[ ] Item ${DEFAULT_TODO_PREVIEW_MAX_ITEMS}`)
  })

  it("omits header when disabled", () => {
    const model = buildTodoPreviewModel([{ id: "1", title: "Only", status: "todo" }], { showHeader: false })
    expect(model?.header).toBe("")
    expect(getTodoPreviewRowCount(model)).toBe(1)
  })
})
