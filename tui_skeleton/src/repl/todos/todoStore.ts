import type { TodoItem, TodoPreviewSelectionStrategy, TodoStoreSnapshot, TodoUpdate } from "../types.js"

export const createEmptyTodoStore = (): TodoStoreSnapshot => ({
  revision: 0,
  updatedAt: 0,
  itemsById: {},
  order: [],
})

const normalizeTitle = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")

const isLikelyFallbackId = (value: string): boolean => /^todo-\d+$/.test(value)

const hashString = (value: string): string => {
  let hash = 0
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(i)
    hash |= 0
  }
  const normalized = Math.abs(hash)
  return normalized.toString(36)
}

const itemsEqual = (a: TodoItem | undefined, b: TodoItem | undefined): boolean => {
  if (!a || !b) return false
  if (a.id !== b.id) return false
  if (a.title !== b.title) return false
  if (a.status !== b.status) return false
  if ((a.priority ?? null) !== (b.priority ?? null)) return false
  // Metadata can be large; for MVP we treat it as an opaque reference.
  if ((a.metadata ?? null) !== (b.metadata ?? null)) return false
  return true
}

export const todoStoreToList = (store: TodoStoreSnapshot): TodoItem[] => {
  const out: TodoItem[] = []
  for (const id of store.order) {
    const item = store.itemsById[id]
    if (item) out.push(item)
  }
  return out
}

export const todoStoreCounts = (store: TodoStoreSnapshot): { done: number; total: number } => {
  const total = store.order.length
  let done = 0
  for (const id of store.order) {
    if (store.itemsById[id]?.status === "done") done += 1
  }
  return { done, total }
}

export const selectTodoPreviewItems = (
  store: TodoStoreSnapshot,
  options: { maxItems: number; strategy: TodoPreviewSelectionStrategy },
): { visible: TodoItem[]; hiddenCount: number } => {
  const maxItems = Math.max(0, Math.floor(options.maxItems))
  const ordered = todoStoreToList(store)
  if (ordered.length <= maxItems) return { visible: ordered, hiddenCount: 0 }

  const strategy = options.strategy
  const select = (items: TodoItem[]): TodoItem[] => items.slice(0, maxItems)

  if (strategy === "first_n") {
    return { visible: select(ordered), hiddenCount: ordered.length - maxItems }
  }

  const buckets: TodoItem[][] =
    strategy === "active_first"
      ? [
          ordered.filter((t) => t.status === "in_progress"),
          ordered.filter((t) => t.status === "todo"),
          ordered.filter((t) => t.status === "blocked"),
          ordered.filter((t) => t.status !== "done" && t.status !== "canceled" && t.status !== "blocked" && t.status !== "in_progress" && t.status !== "todo"),
          ordered.filter((t) => t.status === "done"),
          ordered.filter((t) => t.status === "canceled"),
        ]
      : [
          ordered.filter((t) => t.status !== "done" && t.status !== "canceled"),
          ordered.filter((t) => t.status === "done"),
          ordered.filter((t) => t.status === "canceled"),
        ]

  const selected: TodoItem[] = []
  const seen = new Set<string>()
  for (const bucket of buckets) {
    for (const item of bucket) {
      if (selected.length >= maxItems) break
      if (seen.has(item.id)) continue
      seen.add(item.id)
      selected.push(item)
    }
    if (selected.length >= maxItems) break
  }

  return { visible: selected, hiddenCount: Math.max(0, ordered.length - selected.length) }
}

const equivalent = (a: TodoStoreSnapshot, b: TodoStoreSnapshot): boolean => {
  if (a.order.length !== b.order.length) return false
  for (let i = 0; i < a.order.length; i += 1) {
    if (a.order[i] !== b.order[i]) return false
  }
  for (const id of a.order) {
    if (!itemsEqual(a.itemsById[id], b.itemsById[id])) return false
  }
  return true
}

const bump = (store: TodoStoreSnapshot, at: number, next: Omit<TodoStoreSnapshot, "revision" | "updatedAt">): TodoStoreSnapshot => {
  const candidate: TodoStoreSnapshot = {
    revision: store.revision + 1,
    updatedAt: at,
    itemsById: next.itemsById,
    order: next.order,
  }
  return equivalent(store, candidate) ? store : candidate
}

const normalizeReplaceIds = (store: TodoStoreSnapshot, incoming: ReadonlyArray<TodoItem>): TodoItem[] => {
  const existingByTitle = new Map<string, string[]>()
  for (const id of store.order) {
    const item = store.itemsById[id]
    if (!item) continue
    const key = normalizeTitle(item.title)
    const list = existingByTitle.get(key) ?? []
    list.push(id)
    existingByTitle.set(key, list)
  }

  const consumed = new Set<string>()
  const occurrenceByTitle = new Map<string, number>()
  return incoming.map((item) => {
    const id = item.id
    const titleKey = normalizeTitle(item.title)
    const incomingHasStableId = Boolean(id && !isLikelyFallbackId(id))
    if (incomingHasStableId) {
      consumed.add(id)
      return item
    }

    const candidates = existingByTitle.get(titleKey) ?? []
    const match = candidates.find((candidateId) => !consumed.has(candidateId)) ?? null
    if (match) {
      consumed.add(match)
      return { ...item, id: match }
    }

    const prevCount = occurrenceByTitle.get(titleKey) ?? 0
    const nextCount = prevCount + 1
    occurrenceByTitle.set(titleKey, nextCount)
    const generated = `todo-${hashString(titleKey)}-${nextCount}`
    consumed.add(generated)
    return { ...item, id: generated }
  })
}

export const reduceTodoStore = (store: TodoStoreSnapshot, update: TodoUpdate): TodoStoreSnapshot => {
  switch (update.op) {
    case "clear": {
      return bump(store, update.at, { itemsById: {}, order: [] })
    }
    case "delete": {
      if (!update.ids || update.ids.length === 0) return store
      const itemsById = { ...store.itemsById }
      const remove = new Set(update.ids)
      for (const id of update.ids) {
        delete itemsById[id]
      }
      const order = store.order.filter((id) => !remove.has(id))
      return bump(store, update.at, { itemsById, order })
    }
    case "patch": {
      if (!update.patches || update.patches.length === 0) return store
      const itemsById = { ...store.itemsById }
      let changed = false
      for (const patch of update.patches) {
        const prev = itemsById[patch.id]
        if (!prev) continue
        const next = { ...prev, ...patch.patch, id: prev.id }
        if (!itemsEqual(prev, next)) {
          itemsById[patch.id] = next
          changed = true
        }
      }
      if (!changed) return store
      return bump(store, update.at, { itemsById, order: [...store.order] })
    }
    case "upsert": {
      const item = update.item
      if (!item) return store
      const itemsById = { ...store.itemsById }
      const exists = Boolean(itemsById[item.id])
      itemsById[item.id] = item
      let order = [...store.order]
      if (!exists) {
        const rawPos = update.position ?? null
        const pos = rawPos == null ? null : Math.max(0, Math.min(order.length, Math.floor(rawPos)))
        if (pos == null) order.push(item.id)
        else order.splice(pos, 0, item.id)
      }
      return bump(store, update.at, { itemsById, order })
    }
    case "replace": {
      const incoming = normalizeReplaceIds(store, update.items)
      const itemsById: Record<string, TodoItem> = {}
      const order: string[] = []
      for (const item of incoming) {
        itemsById[item.id] = item
        order.push(item.id)
      }
      return bump(store, update.at, { itemsById, order })
    }
    default:
      return store
  }
}

