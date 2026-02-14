import { describe, expect, it } from "vitest"
import { createEmptyTodoStore, reduceTodoStore, selectTodoPreviewItems, todoStoreToList } from "../todoStore.js"

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
})

