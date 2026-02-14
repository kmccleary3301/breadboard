import { describe, expect, it } from "vitest"
import {
  createEmptyTodoStore,
  reduceTodoStore,
  selectTodoPreviewItems,
  todoStoreCounts,
  todoStoreToList,
} from "../todoStore.js"

describe("reduceTodoStore", () => {
  it("replace assigns stable ids when incoming items have fallback ids and preserves ids across replace", () => {
    const s0 = createEmptyTodoStore()
    const s1 = reduceTodoStore(s0, {
      op: "replace",
      at: 1,
      items: [
        { id: "todo-1", title: "First", status: "todo" },
        { id: "todo-2", title: "Second", status: "done" },
      ],
    })
    expect(s1.revision).toBe(1)
    const firstIds = s1.order
    expect(firstIds).toHaveLength(2)

    const s2 = reduceTodoStore(s1, {
      op: "replace",
      at: 2,
      items: [
        { id: "todo-1", title: "New at top", status: "todo" },
        { id: "todo-2", title: "First", status: "todo" },
        { id: "todo-3", title: "Second", status: "done" },
      ],
    })
    const list2 = todoStoreToList(s2)
    const first2 = list2.find((t) => t.title === "First")
    const second2 = list2.find((t) => t.title === "Second")
    expect(first2?.id).toBe(firstIds[0])
    expect(second2?.id).toBe(firstIds[1])
  })

  it("patch updates fields without reordering", () => {
    const s1 = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: [
        { id: "a", title: "A", status: "todo" },
        { id: "b", title: "B", status: "todo" },
      ],
    })
    const s2 = reduceTodoStore(s1, {
      op: "patch",
      at: 2,
      patches: [{ id: "b", patch: { status: "done" } }],
    })
    expect(s2.order).toEqual(s1.order)
    expect(s2.itemsById["b"]?.status).toBe("done")
  })

  it("delete removes ids from itemsById and order", () => {
    const s1 = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: [
        { id: "a", title: "A", status: "todo" },
        { id: "b", title: "B", status: "todo" },
      ],
    })
    const s2 = reduceTodoStore(s1, { op: "delete", at: 2, ids: ["a"] })
    expect(s2.order).toEqual(["b"])
    expect(s2.itemsById["a"]).toBeUndefined()
  })

  it("does not bump revision on no-op replace", () => {
    const s1 = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: [{ id: "a", title: "A", status: "todo" }],
    })
    const s2 = reduceTodoStore(s1, {
      op: "replace",
      at: 2,
      items: [{ id: "a", title: "A", status: "todo" }],
    })
    expect(s2).toBe(s1)
    expect(s2.revision).toBe(1)
  })
})

describe("selectTodoPreviewItems", () => {
  it("active_first prioritizes in_progress then todo then blocked", () => {
    const store = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: [
        { id: "1", title: "Done", status: "done" },
        { id: "2", title: "Todo", status: "todo" },
        { id: "3", title: "Progress", status: "in_progress" },
        { id: "4", title: "Blocked", status: "blocked" },
      ],
    })
    const selection = selectTodoPreviewItems(store, { maxItems: 3, strategy: "active_first" })
    expect(selection.visible.map((t) => t.id)).toEqual(["3", "2", "4"])
    expect(selection.hiddenCount).toBe(1)
  })

  it("never returns more than maxItems and reports hidden count correctly", () => {
    const store = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: Array.from({ length: 11 }).map((_, idx) => ({
        id: String(idx + 1),
        title: `T${idx + 1}`,
        status: idx % 3 === 0 ? "in_progress" : idx % 3 === 1 ? "todo" : "done",
      })),
    })
    for (const strategy of ["first_n", "incomplete_first", "active_first"] as const) {
      const selection = selectTodoPreviewItems(store, { maxItems: 7, strategy })
      expect(selection.visible.length).toBeLessThanOrEqual(7)
      expect(selection.hiddenCount).toBe(store.order.length - selection.visible.length)
    }
  })
})

describe("todoStoreCounts", () => {
  it("counts done items across the whole store", () => {
    const store = reduceTodoStore(createEmptyTodoStore(), {
      op: "replace",
      at: 1,
      items: [
        { id: "1", title: "A", status: "todo" },
        { id: "2", title: "B", status: "done" },
        { id: "3", title: "C", status: "in_progress" },
        { id: "4", title: "D", status: "done" },
      ],
    })
    expect(todoStoreCounts(store)).toEqual({ done: 2, total: 4 })
  })
})
