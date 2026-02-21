import type { SessionEvent } from "@breadboard/sdk"

const DB_NAME = "breadboard-webapp-v1"
const DB_VERSION = 3
const STORE_NAME = "session_events"
const META_STORE_NAME = "meta"
const SNAPSHOT_STORE_NAME = "session_snapshots"
const META_SCHEMA_KEY = "schema_version"
const CURRENT_SCHEMA_VERSION = "3"

export const EVENT_STORE_LIMITS = {
  maxEventsPerSession: 2000,
  defaultLoadLimit: 800,
} as const

type PersistedEventRow = {
  pk?: number
  session_id: string
  event_id: string
  timestamp: number
  seq: number | null
  event: SessionEvent
}

export type SessionProjectionSnapshot<TProjection = unknown> = {
  session_id: string
  updated_at: number
  event_count: number
  last_event_id: string | null
  projection: TProjection
}

let dbPromise: Promise<IDBDatabase> | null = null

const openDb = (): Promise<IDBDatabase> => {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("indexedDB unavailable"))
      return
    }
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (db.objectStoreNames.contains(STORE_NAME)) {
        db.deleteObjectStore(STORE_NAME)
      }
      if (db.objectStoreNames.contains(META_STORE_NAME)) {
        db.deleteObjectStore(META_STORE_NAME)
      }
      if (db.objectStoreNames.contains(SNAPSHOT_STORE_NAME)) {
        db.deleteObjectStore(SNAPSHOT_STORE_NAME)
      }
      const store = db.createObjectStore(STORE_NAME, { keyPath: "pk", autoIncrement: true })
      store.createIndex("session_id", "session_id", { unique: false })
      store.createIndex("session_event_id", ["session_id", "event_id"], { unique: true })
      const meta = db.createObjectStore(META_STORE_NAME, { keyPath: "key" })
      meta.put({ key: META_SCHEMA_KEY, value: CURRENT_SCHEMA_VERSION })
      const snapshotStore = db.createObjectStore(SNAPSHOT_STORE_NAME, { keyPath: "session_id" })
      snapshotStore.createIndex("updated_at", "updated_at", { unique: false })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error("failed opening indexedDB"))
  })
  return dbPromise
}

const sortRowsByOrder = (rows: PersistedEventRow[]): PersistedEventRow[] => {
  return rows.sort((a, b) => {
    const seqA = a.seq ?? Number.MAX_SAFE_INTEGER
    const seqB = b.seq ?? Number.MAX_SAFE_INTEGER
    if (seqA !== seqB) return seqA - seqB
    if (a.timestamp !== b.timestamp) return a.timestamp - b.timestamp
    const keyA = typeof a.pk === "number" ? a.pk : Number.MAX_SAFE_INTEGER
    const keyB = typeof b.pk === "number" ? b.pk : Number.MAX_SAFE_INTEGER
    return keyA - keyB
  })
}

const compactSessionEvents = async (sessionId: string, max = EVENT_STORE_LIMITS.maxEventsPerSession): Promise<void> => {
  if (max <= 0) return
  try {
    const db = await openDb()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, "readwrite")
      const store = tx.objectStore(STORE_NAME)
      const index = store.index("session_id")
      const request = index.getAll(IDBKeyRange.only(sessionId))

      request.onsuccess = () => {
        const rows = sortRowsByOrder((request.result as PersistedEventRow[]) ?? [])
        const overflow = rows.length - max
        if (overflow <= 0) return
        for (const row of rows.slice(0, overflow)) {
          if (typeof row.pk === "number") {
            store.delete(row.pk)
          }
        }
      }
      request.onerror = () => reject(request.error ?? new Error("indexedDB compaction read failed"))
      tx.onerror = () => reject(tx.error ?? new Error("indexedDB compaction transaction failed"))
      tx.oncomplete = () => resolve()
    })
  } catch {
    // Storage is best-effort only.
  }
}

const withStore = async <T>(
  mode: IDBTransactionMode,
  handler: (store: IDBObjectStore, resolve: (value: T) => void, reject: (reason?: unknown) => void) => void,
): Promise<T> => {
  const db = await openDb()
  return await new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode)
    const store = tx.objectStore(STORE_NAME)
    handler(store, resolve, reject)
    tx.onerror = () => reject(tx.error ?? new Error("indexedDB transaction failed"))
  })
}

export const appendSessionEvent = async (sessionId: string, event: SessionEvent): Promise<void> => {
  try {
    await withStore<void>("readwrite", (store, resolve, reject) => {
      const row: PersistedEventRow = {
        session_id: sessionId,
        event_id: event.id,
        timestamp: event.timestamp,
        seq: event.seq ?? null,
        event,
      }
      const request = store.add(row)
      request.onsuccess = () => resolve()
      request.onerror = () => {
        const errorName = request.error?.name
        if (errorName === "ConstraintError") {
          // Duplicate event IDs can happen after reconnects. Ignore duplicate inserts.
          resolve()
          return
        }
        reject(request.error ?? new Error("indexedDB insert failed"))
      }
    })
    await compactSessionEvents(sessionId, EVENT_STORE_LIMITS.maxEventsPerSession)
  } catch {
    // Storage is best-effort only.
  }
}

export const loadSessionEvents = async (sessionId: string, limit: number = EVENT_STORE_LIMITS.defaultLoadLimit): Promise<SessionEvent[]> => {
  try {
    return await withStore<SessionEvent[]>("readonly", (store, resolve, reject) => {
      const index = store.index("session_id")
      const request = index.getAll(IDBKeyRange.only(sessionId))
      request.onsuccess = () => {
        const rows = sortRowsByOrder((request.result as PersistedEventRow[]) ?? [])
        const boundedLimit = Math.max(1, Math.min(limit, EVENT_STORE_LIMITS.maxEventsPerSession))
        const events = rows
          .slice(-boundedLimit)
          .map((row) => row.event)
        resolve(events)
      }
      request.onerror = () => reject(request.error ?? new Error("indexedDB read failed"))
    })
  } catch {
    return []
  }
}

export const loadSessionTailEvents = async (
  sessionId: string,
  afterEventId: string,
  limit: number = EVENT_STORE_LIMITS.defaultLoadLimit,
): Promise<SessionEvent[]> => {
  try {
    return await withStore<SessionEvent[]>("readonly", (store, resolve, reject) => {
      const index = store.index("session_id")
      const request = index.getAll(IDBKeyRange.only(sessionId))
      request.onsuccess = () => {
        const rows = sortRowsByOrder((request.result as PersistedEventRow[]) ?? [])
        const boundedLimit = Math.max(1, Math.min(limit, EVENT_STORE_LIMITS.maxEventsPerSession))
        const cursor = rows.findIndex((row) => row.event_id === afterEventId)
        const tailRows = cursor >= 0 ? rows.slice(cursor + 1) : rows
        resolve(tailRows.slice(-boundedLimit).map((row) => row.event))
      }
      request.onerror = () => reject(request.error ?? new Error("indexedDB tail read failed"))
    })
  } catch {
    return []
  }
}

export const saveSessionSnapshot = async <TProjection>(
  sessionId: string,
  snapshot: {
    projection: TProjection
    event_count: number
    last_event_id?: string | null
    updated_at?: number
  },
): Promise<void> => {
  try {
    const db = await openDb()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(SNAPSHOT_STORE_NAME, "readwrite")
      const store = tx.objectStore(SNAPSHOT_STORE_NAME)
      const row: SessionProjectionSnapshot<TProjection> = {
        session_id: sessionId,
        projection: snapshot.projection,
        event_count: snapshot.event_count,
        last_event_id: snapshot.last_event_id ?? null,
        updated_at: snapshot.updated_at ?? Date.now(),
      }
      const request = store.put(row)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error ?? new Error("indexedDB snapshot write failed"))
      tx.onerror = () => reject(tx.error ?? new Error("indexedDB snapshot transaction failed"))
    })
  } catch {
    // best-effort
  }
}

export const loadSessionSnapshot = async <TProjection>(sessionId: string): Promise<SessionProjectionSnapshot<TProjection> | null> => {
  try {
    const db = await openDb()
    return await new Promise<SessionProjectionSnapshot<TProjection> | null>((resolve, reject) => {
      const tx = db.transaction(SNAPSHOT_STORE_NAME, "readonly")
      const store = tx.objectStore(SNAPSHOT_STORE_NAME)
      const request = store.get(sessionId)
      request.onsuccess = () => {
        resolve((request.result as SessionProjectionSnapshot<TProjection> | undefined) ?? null)
      }
      request.onerror = () => reject(request.error ?? new Error("indexedDB snapshot read failed"))
      tx.onerror = () => reject(tx.error ?? new Error("indexedDB snapshot transaction failed"))
    })
  } catch {
    return null
  }
}

export const clearEventStore = async (): Promise<void> => {
  try {
    const db = await openDb()
    db.close()
    dbPromise = null
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.deleteDatabase(DB_NAME)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error ?? new Error("failed to delete event store database"))
      request.onblocked = () => resolve()
    })
  } catch {
    // best-effort
  }
}
