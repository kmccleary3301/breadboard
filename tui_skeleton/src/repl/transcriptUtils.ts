import type { ConversationEntry } from "./types.js"

export interface ConversationWindow {
  readonly entries: ConversationEntry[]
  readonly truncated: boolean
  readonly hiddenCount: number
}

export const MAX_TRANSCRIPT_ENTRIES = 120
export const MIN_TRANSCRIPT_ROWS = 4

export const buildConversationWindow = (
  entries: ReadonlyArray<ConversationEntry>,
  capacity: number = MAX_TRANSCRIPT_ENTRIES,
): ConversationWindow => {
  const cap = Math.max(MIN_TRANSCRIPT_ROWS, Math.min(MAX_TRANSCRIPT_ENTRIES, capacity))
  if (entries.length <= cap) {
    return {
      entries: [...entries],
      truncated: false,
      hiddenCount: 0,
    }
  }
  const hiddenCount = entries.length - cap
  return {
    entries: entries.slice(-cap),
    truncated: true,
    hiddenCount,
  }
}

export const findStreamingEntry = (entries: ReadonlyArray<ConversationEntry>): ConversationEntry | undefined => {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index]
    if (entry.phase === "streaming") {
      return entry
    }
  }
  return undefined
}

export const ENTRY_COLLAPSE_THRESHOLD = 24
export const ENTRY_COLLAPSE_HEAD = 8
export const ENTRY_COLLAPSE_TAIL = 4

export interface DiffPreview {
  readonly additions: number
  readonly deletions: number
  readonly files: string[]
}

export const shouldAutoCollapseEntry = (entry: ConversationEntry): boolean => {
  if (entry.speaker === "system") {
    return false
  }
  const lineCount = entry.text.split(/\r?\n/).length
  const hasDiff =
    entry.text.includes("```diff") ||
    entry.text.includes("diff --git") ||
    entry.text.includes("--- ") ||
    entry.text.includes("+++ ") ||
    entry.text.includes("@@ ")
  return lineCount >= ENTRY_COLLAPSE_THRESHOLD || hasDiff
}

const DIFF_GIT_PATTERN = /^diff --git a\/(.+?) b\/(.+)$/
const FILE_PREFIX_NEW = "+++ b/"
const FILE_PREFIX_OLD = "--- a/"

const noteFile = (seen: Set<string>, files: string[], raw: string) => {
  const clean = raw.trim()
  if (!clean || seen.has(clean)) return
  seen.add(clean)
  files.push(clean)
}

export const computeDiffPreview = (lines: ReadonlyArray<string>): DiffPreview | null => {
  let additions = 0
  let deletions = 0
  const files: string[] = []
  const seenFiles = new Set<string>()
  for (const line of lines) {
    if (line.startsWith(FILE_PREFIX_NEW)) {
      noteFile(seenFiles, files, line.slice(FILE_PREFIX_NEW.length))
      continue
    }
    if (line.startsWith(FILE_PREFIX_OLD)) {
      noteFile(seenFiles, files, line.slice(FILE_PREFIX_OLD.length))
      continue
    }
    if (line.startsWith("diff --git")) {
      const match = line.match(DIFF_GIT_PATTERN)
      if (match) {
        noteFile(seenFiles, files, match[2] ?? match[1])
      }
      continue
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      additions += 1
      continue
    }
    if (line.startsWith("-") && !line.startsWith("---")) {
      deletions += 1
    }
  }
  if (additions === 0 && deletions === 0 && files.length === 0) {
    return null
  }
  return {
    additions,
    deletions,
    files: files.slice(0, 3),
  }
}
