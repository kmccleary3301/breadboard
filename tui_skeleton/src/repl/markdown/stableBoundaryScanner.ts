export interface StableBoundaryState {
  readonly inFence: boolean
  readonly fenceMarker: "```" | "~~~" | null
  readonly inList: boolean
  readonly inTable: boolean
}

export interface StableBoundaryResult {
  readonly stableBoundaryLen: number
  readonly prefix: string
  readonly tail: string
  readonly state: StableBoundaryState
}

const DEFAULT_STATE: StableBoundaryState = {
  inFence: false,
  fenceMarker: null,
  inList: false,
  inTable: false,
}

const fenceOpenPattern = /^\s*(```+|~~~+)/
const listPattern = /^\s*(?:[-*+]|\d+\.)\s+/
const tableRowPattern = /^\s*\|.*\|\s*$/
const tableDividerPattern = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/

const scanFenceMarker = (line: string): "```" | "~~~" | null => {
  const match = line.match(fenceOpenPattern)
  if (!match || !match[1]) return null
  return match[1].startsWith("~~~") ? "~~~" : "```"
}

const splitLinesKeepingNewline = (text: string): string[] => {
  if (!text) return []
  const lines = text.match(/[^\n]*\n|[^\n]+$/g)
  return lines ? [...lines] : []
}

export const scanStableBoundary = (
  text: string,
  previous?: Partial<StableBoundaryState> | null,
): StableBoundaryResult => {
  const lines = splitLinesKeepingNewline(text)
  let consumed = 0
  let stableBoundaryLen = 0
  let inFence = previous?.inFence ?? DEFAULT_STATE.inFence
  let fenceMarker = previous?.fenceMarker ?? DEFAULT_STATE.fenceMarker
  let inList = previous?.inList ?? DEFAULT_STATE.inList
  let inTable = previous?.inTable ?? DEFAULT_STATE.inTable

  for (const line of lines) {
    const hasNewline = line.endsWith("\n")
    const bare = hasNewline ? line.slice(0, -1) : line
    const trimmed = bare.trim()

    const marker = scanFenceMarker(bare)
    if (marker) {
      if (!inFence) {
        inFence = true
        fenceMarker = marker
      } else if (fenceMarker === marker) {
        inFence = false
        fenceMarker = null
      }
    }

    if (!inFence) {
      const isTable = tableRowPattern.test(bare) || tableDividerPattern.test(bare)
      const isList = listPattern.test(bare)
      const isBlank = trimmed.length === 0
      inTable = isTable
      inList = isList
      if (isBlank) {
        inTable = false
        inList = false
      }
    }

    consumed += line.length
    if (hasNewline && !inFence) {
      stableBoundaryLen = consumed
    }
  }

  return {
    stableBoundaryLen,
    prefix: text.slice(0, stableBoundaryLen),
    tail: text.slice(stableBoundaryLen),
    state: {
      inFence,
      fenceMarker,
      inList,
      inTable,
    },
  }
}
