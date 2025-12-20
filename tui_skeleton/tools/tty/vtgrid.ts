export interface FrameEntry {
  readonly timestamp: number
  readonly chunk: string
}

export interface LineDeltaEntry {
  readonly timestamp: number
  readonly changedLines: number[]
  readonly bytes: number
}

export interface GridArtifacts {
  readonly grid: string[]
  readonly lastActiveGrid: string[] | null
  readonly deltas: LineDeltaEntry[]
  readonly activeBuffer: "normal" | "alternate"
  readonly normalGrid: string[]
  readonly normalLastActiveGrid: string[] | null
  readonly alternateGrid: string[]
  readonly alternateLastActiveGrid: string[] | null
}

const ESC = "\u001b"
const CSI_FINAL_MIN = 0x40
const CSI_FINAL_MAX = 0x7e
const DEC_ALT_SCREEN_MODES = new Set([47, 1047, 1049])
const DEC_SAVE_CURSOR_MODE = 1048

export const renderGridFromFrames = (
  frames: ReadonlyArray<FrameEntry>,
  rows: number,
  cols: number,
): GridArtifacts => {
  const height = Math.max(rows, 1)
  const width = Math.max(cols, 1)
  const createGrid = () => Array.from({ length: height }, () => Array(width).fill(" "))
  type SavedCursor = { readonly row: number; readonly col: number }
  interface BufferState {
    grid: string[][]
    cursorRow: number
    cursorCol: number
    savedCursor: SavedCursor | null
    lastActiveGrid: string[] | null
  }
  const normal: BufferState = {
    grid: createGrid(),
    cursorRow: 0,
    cursorCol: 0,
    savedCursor: null,
    lastActiveGrid: null,
  }
  const alternate: BufferState = {
    grid: createGrid(),
    cursorRow: 0,
    cursorCol: 0,
    savedCursor: null,
    lastActiveGrid: null,
  }
  let active: BufferState = normal
  let activeBuffer: "normal" | "alternate" = "normal"

  const deltas: LineDeltaEntry[] = []

  const clampRow = (value: number) => Math.min(Math.max(value, 0), height - 1)
  const clampCol = (value: number) => Math.min(Math.max(value, 0), width - 1)

  const markChanged = (set: Set<number>, row: number) => {
    if (row >= 0 && row < height) {
      set.add(row)
    }
  }

  const markAllRowsChanged = (set: Set<number>) => {
    for (let r = 0; r < height; r += 1) {
      set.add(r)
    }
  }

  const clearBuffer = (buffer: BufferState, changed: Set<number>) => {
    for (let r = 0; r < height; r += 1) {
      buffer.grid[r].fill(" ")
    }
    buffer.cursorRow = 0
    buffer.cursorCol = 0
    markAllRowsChanged(changed)
  }

  const switchToBuffer = (target: "normal" | "alternate", changed: Set<number>, options?: { clear?: boolean }) => {
    if (target === activeBuffer) {
      if (options?.clear) {
        clearBuffer(active, changed)
      }
      return
    }
    activeBuffer = target
    active = target === "normal" ? normal : alternate
    if (options?.clear) {
      clearBuffer(active, changed)
      return
    }
    markAllRowsChanged(changed)
  }

  const saveCursor = (buffer: BufferState) => {
    buffer.savedCursor = { row: buffer.cursorRow, col: buffer.cursorCol }
  }

  const restoreCursor = (buffer: BufferState) => {
    if (!buffer.savedCursor) return
    buffer.cursorRow = clampRow(buffer.savedCursor.row)
    buffer.cursorCol = clampCol(buffer.savedCursor.col)
  }

  const scrollIfNeeded = () => {
    if (active.cursorRow < height) return
    active.grid.shift()
    active.grid.push(Array(width).fill(" "))
    active.cursorRow = height - 1
  }

  const writeChar = (char: string, changed: Set<number>) => {
    if (char === "\n") {
      active.cursorRow += 1
      scrollIfNeeded()
      return
    }
    if (char === "\r") {
      active.cursorCol = 0
      return
    }
    if (char === "\t") {
      const spaces = 4
      for (let i = 0; i < spaces; i += 1) {
        writeChar(" ", changed)
      }
      return
    }
    active.grid[active.cursorRow][active.cursorCol] = char
    markChanged(changed, active.cursorRow)
    active.cursorCol += 1
    if (active.cursorCol >= width) {
      active.cursorCol = 0
      active.cursorRow += 1
      scrollIfNeeded()
    }
  }

  const clearLineFrom = (col: number, changed: Set<number>) => {
    const target = active.grid[active.cursorRow]
    for (let i = col; i < width; i += 1) {
      target[i] = " "
    }
    markChanged(changed, active.cursorRow)
  }

  const clearDisplay = (mode: number | undefined, changed: Set<number>) => {
    if (mode === 1) {
      for (let r = 0; r <= active.cursorRow; r += 1) {
        active.grid[r].fill(" ")
        markChanged(changed, r)
      }
      return
    }
    if (mode === 2 || mode === 3) {
      for (let r = 0; r < height; r += 1) {
        active.grid[r].fill(" ")
        markChanged(changed, r)
      }
      active.cursorRow = 0
      active.cursorCol = 0
      return
    }
    if (mode === 0 || mode === undefined) {
      for (let r = active.cursorRow; r < height; r += 1) {
        const startCol = r === active.cursorRow ? active.cursorCol : 0
        for (let c = startCol; c < width; c += 1) {
          active.grid[r][c] = " "
        }
        markChanged(changed, r)
      }
    }
  }

  const handleDecPrivateMode = (params: number[], enable: boolean, changed: Set<number>) => {
    for (const mode of params) {
      if (!Number.isFinite(mode)) continue
      if (DEC_ALT_SCREEN_MODES.has(mode)) {
        if (enable) {
          if (mode === 1049) {
            saveCursor(normal)
          }
          switchToBuffer("alternate", changed, { clear: true })
        } else {
          switchToBuffer("normal", changed)
          if (mode === 1049) {
            restoreCursor(normal)
          }
        }
        continue
      }
      if (mode === DEC_SAVE_CURSOR_MODE) {
        if (enable) {
          saveCursor(active)
        } else {
          restoreCursor(active)
        }
      }
    }
  }

  const executeCsi = (command: string, params: number[], changed: Set<number>, options?: { private?: boolean }) => {
    switch (command) {
      case "H":
      case "f": {
        const targetRow = params[0] ?? 1
        const targetCol = params[1] ?? 1
        active.cursorRow = clampRow(targetRow - 1)
        active.cursorCol = clampCol(targetCol - 1)
        break
      }
      case "A":
        active.cursorRow = clampRow(active.cursorRow - (params[0] ?? 1))
        break
      case "B":
        active.cursorRow = clampRow(active.cursorRow + (params[0] ?? 1))
        break
      case "C":
        active.cursorCol = clampCol(active.cursorCol + (params[0] ?? 1))
        break
      case "D":
        active.cursorCol = clampCol(active.cursorCol - (params[0] ?? 1))
        break
      case "J":
        clearDisplay(params[0], changed)
        break
      case "K":
        clearLineFrom(active.cursorCol, changed)
        break
      case "m":
        break
      case "h":
      case "l":
        if (options?.private) {
          handleDecPrivateMode(params, command === "h", changed)
        }
        break
      default:
        break
    }
  }

  for (const frame of frames) {
    const changed = new Set<number>()
    let i = 0
    const chunk = frame.chunk
    while (i < chunk.length) {
      const char = chunk[i]
      if (char === ESC) {
        const next = chunk[i + 1]
        if (next === "[") {
          i += 2
          let seq = ""
          while (i < chunk.length) {
            const code = chunk.charCodeAt(i)
            seq += chunk[i]
            i += 1
            if (code >= CSI_FINAL_MIN && code <= CSI_FINAL_MAX) {
              break
            }
          }
          const finalChar = seq.slice(-1)
          const body = seq.slice(0, -1)
          const isPrivate = body.startsWith("?")
          const bodyWithoutPrivate = isPrivate ? body.slice(1) : body
          const rawParams = bodyWithoutPrivate.split(";").filter((part) => part.length > 0)
          const params = rawParams.map((value) => Number.parseInt(value, 10)).filter((value) => !Number.isNaN(value))
          executeCsi(finalChar, params, changed, { private: isPrivate })
        } else {
          i += 1
        }
        continue
      }
      writeChar(char, changed)
      i += 1
    }
    if (changed.size > 0) {
      deltas.push({
        timestamp: frame.timestamp,
        changedLines: Array.from(changed.values()).sort((a, b) => a - b),
        bytes: frame.chunk.length,
      })
      const snapshot = active.grid.map((row) => row.join(""))
      if (snapshot.some((line) => line.trim().length > 0)) {
        active.lastActiveGrid = snapshot
      }
    }
  }

  return {
    grid: active.grid.map((row) => row.join("")),
    lastActiveGrid: active.lastActiveGrid,
    deltas,
    activeBuffer,
    normalGrid: normal.grid.map((row) => row.join("")),
    normalLastActiveGrid: normal.lastActiveGrid,
    alternateGrid: alternate.grid.map((row) => row.join("")),
    alternateLastActiveGrid: alternate.lastActiveGrid,
  }
}
