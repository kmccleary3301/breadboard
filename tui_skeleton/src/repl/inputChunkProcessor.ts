const BRACKET_START = "\u001B[200~"
const BRACKET_END = "\u001B[201~"

export interface PasteTracker {
  capturing: boolean
  buffer: string
  pendingStart: string
  burstActive: boolean
  burstBuffer: string
  lastPlainTimestamp: number
}

export interface ChunkCallbacks {
  insertText: (text: string) => void
  submit: () => void
  deleteWordBackward: () => void
  deleteBackward: () => void
  handlePastePayload: (payload: string) => void
}

export interface KeyModifiers {
  ctrl: boolean
  meta: boolean
}

export const createPasteTracker = (): PasteTracker => ({
  capturing: false,
  buffer: "",
  pendingStart: "",
  burstActive: false,
  burstBuffer: "",
  lastPlainTimestamp: 0,
})

export const resetPasteTracker = (tracker: PasteTracker): void => {
  tracker.capturing = false
  tracker.buffer = ""
  tracker.pendingStart = ""
  tracker.burstActive = false
  tracker.burstBuffer = ""
  tracker.lastPlainTimestamp = 0
}

export const shouldTriggerClipboardPaste = (key: KeyModifiers, normalizedInput: string): boolean =>
  (normalizedInput === "v" && (key.ctrl || key.meta)) ||
  normalizedInput === "\u0016"

const isPrintable = (char: string) => char >= " "
const BURST_INTERVAL_MS = 12
const BURST_MIN_CHARS = 3

const nowMs = () => Number(process.hrtime.bigint() / 1_000_000n)

const flushBurst = (tracker: PasteTracker, callbacks: ChunkCallbacks) => {
  if (!tracker.burstActive) return
  const payload = tracker.burstBuffer
  tracker.burstActive = false
  tracker.burstBuffer = ""
  if (!payload) return
  if (payload.length >= BURST_MIN_CHARS) {
    callbacks.insertText(payload)
  } else {
    for (const char of payload) {
      callbacks.insertText(char)
    }
  }
}

const maybeFlushBurst = (tracker: PasteTracker, callbacks: ChunkCallbacks, currentTime: number) => {
  if (!tracker.burstActive) return
  const sinceLast = tracker.lastPlainTimestamp ? currentTime - tracker.lastPlainTimestamp : Number.POSITIVE_INFINITY
  if (sinceLast > BURST_INTERVAL_MS) {
    flushBurst(tracker, callbacks)
    tracker.lastPlainTimestamp = 0
  }
}

const longestStartPrefix = (text: string): number => {
  const max = Math.min(BRACKET_START.length - 1, text.length)
  for (let length = max; length > 0; length -= 1) {
    if (BRACKET_START.startsWith(text.slice(text.length - length))) {
      return length
    }
  }
  return 0
}

export const processInputChunk = (
  chunk: string,
  tracker: PasteTracker,
  key: KeyModifiers,
  callbacks: ChunkCallbacks,
): boolean => {
  const handleChars = (text: string): boolean => {
    if (!text) return true
    let handledAll = true
    for (const char of text) {
      const lowerChar = char.toLowerCase()
      if (char === "\r" || char === "\n") {
        flushBurst(tracker, callbacks)
        callbacks.submit()
        continue
      }
      if (char === "\u0017" || (key.ctrl && lowerChar === "w" && char.length === 1)) {
        flushBurst(tracker, callbacks)
        callbacks.deleteWordBackward()
        continue
      }
      if (char === "\u0008" || char === "\u007f") {
        flushBurst(tracker, callbacks)
        callbacks.deleteBackward()
        continue
      }
      if (isPrintable(char) && !key.ctrl && !key.meta) {
        const currentTime = nowMs()
        const startBurst =
          tracker.burstActive ||
          text.length > 1 ||
          (tracker.lastPlainTimestamp > 0 && currentTime - tracker.lastPlainTimestamp <= BURST_INTERVAL_MS)
        if (startBurst) {
          tracker.burstActive = true
          tracker.burstBuffer += char
        } else {
          callbacks.insertText(char)
        }
        tracker.lastPlainTimestamp = currentTime
        continue
      }
      handledAll = false
    }
    flushBurst(tracker, callbacks)
    return handledAll
  }

  const process = (text: string): boolean => {
    if (!text) return true
    maybeFlushBurst(tracker, callbacks, nowMs())

    if (tracker.capturing) {
      tracker.buffer += text
      while (true) {
        const endIndex = tracker.buffer.indexOf(BRACKET_END)
        if (endIndex === -1) {
          return true
        }
        const payload = tracker.buffer.slice(0, endIndex)
        const remainder = tracker.buffer.slice(endIndex + BRACKET_END.length)
        tracker.capturing = false
        tracker.buffer = ""
        callbacks.handlePastePayload(payload)
        if (!process(remainder)) {
          return false
        }
        return true
      }
    }

    let combined = tracker.pendingStart + text
    tracker.pendingStart = ""

    while (combined.length > 0) {
      const startIndex = combined.indexOf(BRACKET_START)
      if (startIndex === -1) {
        const prefixLength = longestStartPrefix(combined)
        const processableLength = combined.length - prefixLength
        if (processableLength > 0) {
          const segment = combined.slice(0, processableLength)
          if (!handleChars(segment)) {
            return false
          }
        }
        tracker.pendingStart = combined.slice(combined.length - prefixLength)
        return true
      }

      const before = combined.slice(0, startIndex)
      if (!handleChars(before)) {
        return false
      }
      flushBurst(tracker, callbacks)
      combined = combined.slice(startIndex + BRACKET_START.length)
      tracker.capturing = true
      tracker.buffer = ""
      if (!process(combined)) {
        return false
      }
      return true
    }

    return true
  }

  return process(chunk)
}

export { BRACKET_START, BRACKET_END }
