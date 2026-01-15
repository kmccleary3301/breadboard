export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

export const encodeLine = (value: unknown): string => `${JSON.stringify(value)}\n`

export const createNdjsonParser = (onObject: (obj: unknown) => void) => {
  let buffer = ""

  return {
    push(chunk: string) {
      buffer += chunk
      for (;;) {
        const idx = buffer.indexOf("\n")
        if (idx === -1) break
        const line = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 1)
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          onObject(JSON.parse(trimmed))
        } catch {
          // ignore malformed lines
        }
      }
    },
    flush() {
      const trimmed = buffer.trim()
      buffer = ""
      if (!trimmed) return
      try {
        onObject(JSON.parse(trimmed))
      } catch {
        // ignore
      }
    },
  }
}

