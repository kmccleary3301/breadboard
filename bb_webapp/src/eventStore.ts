import type { SessionEvent } from "@breadboard/sdk"

const DB_NAME = "breadboard-webapp-v1"
const DB_VERSION = 1
const STORE_NAME = "session_events"

type PersistedEventRow = {
  pk?: number
  session_id: string
  event_id: string
  timestamp: number
  seq: number | null
  event: SessionEvent
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
      const store = db.createObjectStore(STORE_NAME, { keyPath: "pk", autoIncrement: true })
      store.createIndex("session_id", "session_id", { unique: false })
      store.createIndex("session_event_id", ["session_id", "event_id"], { unique: true })
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error("failed opening indexedDB"))
  })
  return dbPromise
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
  } catch {
    // Storage is best-effort only.
  }
}

export const loadSessionEvents = async (sessionId: string, limit = 800): Promise<SessionEvent[]> => {
  try {
    return await withStore<SessionEvent[]>("readonly", (store, resolve, reject) => {
      const index = store.index("session_id")
      const request = index.getAll(IDBKeyRange.only(sessionId))
      request.onsuccess = () => {
        const rows = (request.result as PersistedEventRow[]) ?? []
        const events = rows
          .sort((a, b) => {
            const seqA = a.seq ?? Number.MAX_SAFE_INTEGER
            const seqB = b.seq ?? Number.MAX_SAFE_INTEGER
            if (seqA !== seqB) return seqA - seqB
            return a.timestamp - b.timestamp
          })
          .slice(-limit)
          .map((row) => row.event)
        resolve(events)
      }
      request.onerror = () => reject(request.error ?? new Error("indexedDB read failed"))
    })
  } catch {
    return []
  }
}
