export const ALT_BUFFER_ENTER = "\u001b[?1049h"
export const ALT_BUFFER_EXIT = "\u001b[?1049l"

export type AltBufferWriter = (chunk: string) => void

export interface AltBufferSession {
  readonly enabled: boolean
  isActive(): boolean
  sync(desired: boolean): void
  reset(): void
}

export const createAltBufferSession = (writer: AltBufferWriter, enabled: boolean): AltBufferSession => {
  let active = false
  const write = (chunk: string): void => {
    try {
      writer(chunk)
    } catch {
      // Best-effort terminal capability: ignore write failures and fall back safely.
    }
  }
  const setState = (next: boolean): void => {
    if (!enabled) next = false
    if (next === active) return
    write(next ? ALT_BUFFER_ENTER : ALT_BUFFER_EXIT)
    active = next
  }
  return {
    enabled,
    isActive: () => active,
    sync: (desired: boolean) => setState(Boolean(desired)),
    reset: () => setState(false),
  }
}

