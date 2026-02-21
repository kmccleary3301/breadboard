import type { SessionEvent } from "@breadboard/sdk"

type CanonicalValue = null | boolean | number | string | CanonicalValue[] | { [key: string]: CanonicalValue }

const canonicalize = (value: unknown): CanonicalValue => {
  if (value == null) return null
  if (typeof value === "boolean" || typeof value === "number" || typeof value === "string") return value
  if (Array.isArray(value)) return value.map((item) => canonicalize(item))
  if (typeof value === "object") {
    const source = value as Record<string, unknown>
    const out: Record<string, CanonicalValue> = {}
    for (const key of Object.keys(source).sort()) {
      out[key] = canonicalize(source[key])
    }
    return out
  }
  return String(value)
}

const toCanonicalString = (events: SessionEvent[]): string => {
  return JSON.stringify(
    events.map((event) =>
      canonicalize({
        id: event.id,
        seq: event.seq ?? null,
        type: event.type,
        session_id: event.session_id,
        turn: event.turn ?? null,
        timestamp: event.timestamp,
        payload: event.payload ?? {},
      }),
    ),
  )
}

const fnv1aHex = (text: string): string => {
  let hash = 0x811c9dc5
  for (let idx = 0; idx < text.length; idx += 1) {
    hash ^= text.charCodeAt(idx)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(16).padStart(8, "0")
}

const bytesToHex = (bytes: Uint8Array): string => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")

export const computeProjectionHash = async (events: SessionEvent[]): Promise<string> => {
  const canonical = toCanonicalString(events)
  const subtle = globalThis.crypto?.subtle
  if (!subtle) {
    return `fnv1a:${fnv1aHex(canonical)}`
  }
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(canonical))
  return `sha256:${bytesToHex(new Uint8Array(digest))}`
}
