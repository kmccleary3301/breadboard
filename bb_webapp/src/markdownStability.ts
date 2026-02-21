export type StableMarkdownSplit = {
  stablePrefix: string
  unstableTail: string
}

type FenceState = {
  inFence: boolean
  markerChar: "`" | "~" | null
  markerLength: number
}

const splitLinesWithNewline = (text: string): string[] => {
  if (!text) return []
  const parts = text.split("\n")
  const out: string[] = []
  for (let index = 0; index < parts.length; index += 1) {
    const isLast = index === parts.length - 1
    out.push(isLast ? parts[index] : `${parts[index]}\n`)
  }
  return out
}

const readFenceMarker = (lineWithoutNewline: string): { char: "`" | "~"; length: number } | null => {
  const trimmed = lineWithoutNewline.trimStart()
  const match = /^(?<marker>`{3,}|~{3,})/.exec(trimmed)
  if (!match?.groups?.marker) return null
  const marker = match.groups.marker
  const char = marker[0]
  if (char !== "`" && char !== "~") return null
  return { char, length: marker.length }
}

const isSafePunctuationBoundary = (char: string): boolean => /[\s.,;:!?)]/.test(char)

export const splitStableMarkdownForStreaming = (text: string): StableMarkdownSplit => {
  if (!text) return { stablePrefix: "", unstableTail: "" }

  const lines = splitLinesWithNewline(text)
  const fence: FenceState = { inFence: false, markerChar: null, markerLength: 0 }
  let inlineCodeOpen = false
  let bracketDepth = 0
  let parenDepth = 0
  let offset = 0
  let stableBoundary = 0

  for (const line of lines) {
    const hasTrailingNewline = line.endsWith("\n")
    const content = hasTrailingNewline ? line.slice(0, -1) : line
    const fenceMarker = readFenceMarker(content)
    const isFenceToggleLine = fenceMarker !== null

    if (fenceMarker) {
      if (!fence.inFence) {
        fence.inFence = true
        fence.markerChar = fenceMarker.char
        fence.markerLength = fenceMarker.length
      } else if (fence.markerChar === fenceMarker.char && fenceMarker.length >= fence.markerLength) {
        fence.inFence = false
      }
    }

    if (!fence.inFence && !isFenceToggleLine) {
      for (let idx = 0; idx < content.length; idx += 1) {
        const char = content[idx]
        if (char === "`") {
          let run = 1
          while (idx + 1 < content.length && content[idx + 1] === "`") {
            run += 1
            idx += 1
          }
          if (run % 2 === 1) {
            inlineCodeOpen = !inlineCodeOpen
          }
          continue
        }
        if (char === "[") {
          bracketDepth += 1
        } else if (char === "]") {
          bracketDepth = Math.max(0, bracketDepth - 1)
        } else if (char === "(") {
          parenDepth += 1
        } else if (char === ")") {
          parenDepth = Math.max(0, parenDepth - 1)
        }

        if (!inlineCodeOpen && bracketDepth === 0 && parenDepth === 0 && isSafePunctuationBoundary(char)) {
          stableBoundary = Math.max(stableBoundary, offset + idx + 1)
        }
      }
    }

    if (hasTrailingNewline && !fence.inFence && !inlineCodeOpen && bracketDepth === 0 && parenDepth === 0) {
      stableBoundary = Math.max(stableBoundary, offset + line.length)
    }

    if (!hasTrailingNewline && !fence.inFence && !inlineCodeOpen && bracketDepth === 0 && parenDepth === 0) {
      const lastChar = content.length > 0 ? content[content.length - 1] : ""
      if (lastChar && isSafePunctuationBoundary(lastChar)) {
        stableBoundary = Math.max(stableBoundary, offset + content.length)
      }
    }

    offset += line.length
  }

  return {
    stablePrefix: text.slice(0, stableBoundary),
    unstableTail: text.slice(stableBoundary),
  }
}
