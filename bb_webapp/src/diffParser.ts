export type DiffLineType = "context" | "add" | "remove"

export type DiffLine = {
  type: DiffLineType
  text: string
  oldNumber: number | null
  newNumber: number | null
}

export type DiffHunk = {
  header: string
  lines: DiffLine[]
}

export type DiffFile = {
  oldPath: string
  newPath: string
  displayPath: string
  hunks: DiffHunk[]
  additions: number
  removals: number
}

export type ParsedUnifiedDiff = {
  files: DiffFile[]
  truncated: boolean
  malformed: boolean
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value)

const readString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null

const normalizePath = (raw: string): string => raw.replace(/^a\//, "").replace(/^b\//, "")

export const extractDiffPayload = (payload: unknown): { diffText: string; filePath: string | null } | null => {
  if (!isRecord(payload)) return null
  const diffText =
    readString(payload.diff) ??
    readString(payload.patch) ??
    readString(payload.unified_diff) ??
    readString(payload.diff_text)
  if (!diffText) return null
  const path = readString(payload.path) ?? readString(payload.file) ?? readString(payload.filepath) ?? null
  return { diffText, filePath: path }
}

const parseHeaderRangeStart = (header: string, marker: "-" | "+"): number => {
  const regex = marker === "-" ? /@@ -(\d+)(?:,\d+)?/ : /\+(\d+)(?:,\d+)? @@/
  const match = regex.exec(header)
  if (!match) return 0
  const parsed = Number(match[1])
  return Number.isFinite(parsed) ? parsed : 0
}

export const parseUnifiedDiff = (text: string, maxLines = 4000): ParsedUnifiedDiff => {
  const lines = text.split("\n")
  const truncated = lines.length > maxLines
  const source = truncated ? lines.slice(0, maxLines) : lines
  const files: DiffFile[] = []

  let currentFile: DiffFile | null = null
  let currentHunk: DiffHunk | null = null
  let oldLine = 0
  let newLine = 0
  let malformed = false

  const pushFile = (): void => {
    if (!currentFile) return
    if (currentHunk) {
      currentFile.hunks.push(currentHunk)
      currentHunk = null
    }
    files.push(currentFile)
    currentFile = null
  }

  for (let index = 0; index < source.length; index += 1) {
    const line = source[index]
    if (line.startsWith("--- ")) {
      pushFile()
      const oldPath = line.slice(4).trim()
      const next = source[index + 1] ?? ""
      const newPath = next.startsWith("+++ ") ? next.slice(4).trim() : oldPath
      if (next.startsWith("+++ ")) {
        index += 1
      } else {
        malformed = true
      }
      currentFile = {
        oldPath,
        newPath,
        displayPath: normalizePath(newPath || oldPath),
        hunks: [],
        additions: 0,
        removals: 0,
      }
      continue
    }

    if (line.startsWith("@@ ")) {
      if (!currentFile) {
        currentFile = {
          oldPath: "unknown",
          newPath: "unknown",
          displayPath: "unknown",
          hunks: [],
          additions: 0,
          removals: 0,
        }
        malformed = true
      }
      if (currentHunk) currentFile.hunks.push(currentHunk)
      currentHunk = { header: line, lines: [] }
      oldLine = parseHeaderRangeStart(line, "-")
      newLine = parseHeaderRangeStart(line, "+")
      continue
    }

    if (!currentHunk) {
      if (line.trim().length > 0) malformed = true
      continue
    }

    const prefix = line[0] ?? ""
    const body = line.slice(prefix ? 1 : 0)
    if (prefix === "+") {
      currentFile!.additions += 1
      currentHunk.lines.push({ type: "add", text: body, oldNumber: null, newNumber: newLine || null })
      newLine += 1
      continue
    }
    if (prefix === "-") {
      currentFile!.removals += 1
      currentHunk.lines.push({ type: "remove", text: body, oldNumber: oldLine || null, newNumber: null })
      oldLine += 1
      continue
    }
    currentHunk.lines.push({ type: "context", text: body, oldNumber: oldLine || null, newNumber: newLine || null })
    oldLine += 1
    newLine += 1
  }

  pushFile()

  if (files.length === 0) {
    return {
      files: [
        {
          oldPath: "raw",
          newPath: "raw",
          displayPath: "raw",
          hunks: [{ header: "@@ raw @@", lines: source.map((line) => ({ type: "context", text: line, oldNumber: null, newNumber: null })) }],
          additions: 0,
          removals: 0,
        },
      ],
      truncated,
      malformed: true,
    }
  }

  return { files, truncated, malformed }
}
