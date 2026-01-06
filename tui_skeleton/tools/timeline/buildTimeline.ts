import { promises as fs } from "node:fs"
import path from "node:path"

interface TimelineEntry {
  readonly t: number | null
  readonly stream: "pty" | "sse"
  readonly data: Record<string, unknown>
}

interface ResizeStats {
  readonly count: number
  readonly minCols: number
  readonly maxCols: number
  readonly minRows: number
  readonly maxRows: number
  readonly burstMs: number
}

interface TimelineSummary {
  readonly ptyEvents: number
  readonly totalLinesChanged: number
  readonly totalBytes: number
  readonly sseEvents: number
  readonly warnings: string[]
  readonly ghostLines: number | null
  readonly ttftSeconds: number | null
  readonly spinnerHz: number | null
  readonly linesPerEvent: number | null
  readonly lineDiff?: {
    readonly rows: number | null
    readonly maxLinesChanged: number | null
    readonly maxLinesChangedPct: number | null
    readonly p95LinesChangedPct: number | null
    readonly p99LinesChangedPct: number | null
    readonly meanLinesChangedPct: number | null
    readonly flickerLineEvents: number | null
  }
  readonly chaos: Record<string, unknown> | null
  readonly resizeStats?: ResizeStats | null
}

const readJsonIfExists = async <T>(filePath: string | null | undefined): Promise<T | null> => {
  if (!filePath) return null
  try {
    const contents = await fs.readFile(filePath, "utf8")
    if (!contents.trim()) return null
    return JSON.parse(contents) as T
  } catch (error) {
    return null
  }
}

const readLines = async (filePath: string | null | undefined): Promise<string[]> => {
  if (!filePath) return []
  try {
    const contents = await fs.readFile(filePath, "utf8")
    return contents.split(/\r?\n/)
  } catch {
    return []
  }
}

const parsePtyDeltas = async (gridDeltaPath: string | null | undefined) => {
  const lines = await readLines(gridDeltaPath)
  const events = [] as Array<{ timestamp: number | null; changedLines: number[]; bytes: number }>
  for (const line of lines) {
    if (!line.trim()) continue
    try {
      const value = JSON.parse(line) as { timestamp?: number; changedLines?: number[]; bytes?: number }
      events.push({
        timestamp: typeof value.timestamp === "number" ? value.timestamp : null,
        changedLines: Array.isArray(value.changedLines) ? value.changedLines : [],
        bytes: typeof value.bytes === "number" ? value.bytes : 0,
      })
    } catch {
      events.push({ timestamp: null, changedLines: [], bytes: 0 })
    }
  }
  return events
}

const parseSseEvents = async (ssePath: string | null | undefined) => {
  const lines = await readLines(ssePath)
  const events: Array<Record<string, unknown>> = []
  let buffer = ""
  for (const line of lines) {
    if (line.startsWith("data:")) {
      buffer += line.slice(5).trim()
      continue
    }
    if (line.trim().length === 0 && buffer.length > 0) {
      try {
        events.push(JSON.parse(buffer))
      } catch {
        // ignore malformed entries
      }
      buffer = ""
    }
  }
  if (buffer.length > 0) {
    try {
      events.push(JSON.parse(buffer))
    } catch {
      // ignore trailing malformed entry
    }
  }
  return events
}

interface TimelineOptions {
  readonly metadataPath?: string | null
  readonly gridDeltaPath?: string | null
  readonly ssePath?: string | null
  readonly anomaliesPath?: string | null
  readonly chaosInfo?: Record<string, unknown> | null
}

export const buildTimelineArtifacts = async (caseDir: string, options?: TimelineOptions) => {
  const metadataPath = options?.metadataPath ?? path.join(caseDir, "pty_metadata.json")
  const gridDeltasPath = options?.gridDeltaPath ?? path.join(caseDir, "grid_deltas.ndjson")
  const ssePath = options?.ssePath ?? path.join(caseDir, "sse_events.txt")
  const anomaliesPath = options?.anomaliesPath ?? path.join(caseDir, "anomalies.json")
  const timelinePath = path.join(caseDir, "timeline.ndjson")
  const summaryPath = path.join(caseDir, "timeline_summary.json")

  const metadata = await readJsonIfExists<{
    startedAt?: number
    resizeStats?: Record<string, unknown>
    rows?: number
    cols?: number
  }>(metadataPath)
  const startedAt = typeof metadata?.startedAt === "number" ? metadata.startedAt : null
  const gridRows = typeof metadata?.rows === "number" && Number.isFinite(metadata.rows) ? metadata.rows : null
  const normalizeResizeStats = (value: Record<string, unknown> | null | undefined): ResizeStats | null => {
    if (!value) return null
    const count = Number(value.count ?? 0)
    const minCols = Number(value.minCols ?? 0)
    const maxCols = Number(value.maxCols ?? 0)
    const minRows = Number(value.minRows ?? 0)
    const maxRows = Number(value.maxRows ?? 0)
    const burstMs = Number(value.burstMs ?? 0)
    if ([count, minCols, maxCols, minRows, maxRows, burstMs].some((num) => !Number.isFinite(num))) {
      return null
    }
    return {
      count,
      minCols,
      maxCols,
      minRows,
      maxRows,
      burstMs,
    }
  }
  const normalizeChaos = (value: Record<string, unknown> | null | undefined) => {
    if (!value) return null
    const result: Record<string, unknown> = {}
    for (const [key, raw] of Object.entries(value)) {
      if (raw == null) continue
      if (typeof raw === "number" || typeof raw === "string" || typeof raw === "boolean") {
        result[key] = raw
      }
    }
    return Object.keys(result).length > 0 ? result : null
  }
  const resizeStats = normalizeResizeStats(metadata?.resizeStats)

  const ptyEvents = await parsePtyDeltas(gridDeltasPath)
  const sseEvents = await parseSseEvents(ssePath)
  const anomalies = await readJsonIfExists<Array<{ id?: string }>>(anomaliesPath)
  const ghostLines = Array.isArray(anomalies)
    ? anomalies.filter((entry) => typeof entry?.id === "string" && entry.id.includes("duplicate")).length
    : null

  const timeline: TimelineEntry[] = []
  const warnings: string[] = []

  for (const event of ptyEvents) {
    timeline.push({
      t: startedAt != null && event.timestamp != null ? (event.timestamp - startedAt) / 1000 : null,
      stream: "pty",
      data: {
        changedLines: event.changedLines.length,
        bytes: event.bytes,
      },
    })
  }

  if (!sseEvents.length) {
    warnings.push("no-sse-events")
  }

  let seq = 0
  for (const payload of sseEvents) {
    timeline.push({
      t: null,
      stream: "sse",
      data: {
        seq,
        event: typeof payload === "object" && payload && "type" in payload ? (payload as any).type : undefined,
      },
    })
    seq += 1
  }

  const totalLinesChanged = ptyEvents.reduce((acc, event) => acc + event.changedLines.length, 0)
  const totalBytes = ptyEvents.reduce((acc, event) => acc + event.bytes, 0)
  const lineChangeCounts = ptyEvents.map((event) => event.changedLines.length)
  const maxLinesChanged = lineChangeCounts.length > 0 ? Math.max(...lineChangeCounts) : 0
  const meanLinesChanged = lineChangeCounts.length > 0 ? totalLinesChanged / lineChangeCounts.length : 0

  const buildPercentile = (values: number[], percentile: number) => {
    if (values.length === 0) return null
    const sorted = [...values].sort((a, b) => a - b)
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(percentile * sorted.length) - 1))
    return sorted[index]
  }

  let flickerLineEvents = 0
  const lastLineChange = new Map<number, number>()
  ptyEvents.forEach((event, index) => {
    for (const line of event.changedLines) {
      const last = lastLineChange.get(line)
      if (last === index - 1) {
        flickerLineEvents += 1
      }
      lastLineChange.set(line, index)
    }
  })

  const lineDiff = (() => {
    if (!gridRows || gridRows <= 0) {
      return {
        rows: gridRows ?? null,
        maxLinesChanged,
        maxLinesChangedPct: null,
        p95LinesChangedPct: null,
        p99LinesChangedPct: null,
        meanLinesChangedPct: null,
        flickerLineEvents: lineChangeCounts.length > 0 ? flickerLineEvents : null,
      }
    }
    const toPct = (value: number | null) => (value == null ? null : value / gridRows)
    return {
      rows: gridRows,
      maxLinesChanged,
      maxLinesChangedPct: toPct(maxLinesChanged),
      p95LinesChangedPct: toPct(buildPercentile(lineChangeCounts, 0.95)),
      p99LinesChangedPct: toPct(buildPercentile(lineChangeCounts, 0.99)),
      meanLinesChangedPct: toPct(meanLinesChanged),
      flickerLineEvents: lineChangeCounts.length > 0 ? flickerLineEvents : null,
    }
  })()

  const firstPtyTimestamp = ptyEvents.find((event) => event.timestamp != null)?.timestamp ?? null
  const lastPtyTimestamp = [...ptyEvents].reverse().find((event) => event.timestamp != null)?.timestamp ?? null
  const durationMs =
    firstPtyTimestamp != null && lastPtyTimestamp != null && lastPtyTimestamp > firstPtyTimestamp
      ? lastPtyTimestamp - firstPtyTimestamp
      : null
  const spinnerHz = durationMs && durationMs > 0 ? (ptyEvents.length / (durationMs / 1000)) : null
  const linesPerEvent = ptyEvents.length > 0 ? totalLinesChanged / ptyEvents.length : null

  const firstSseTimestamp = (() => {
    for (const event of sseEvents) {
      const ts = typeof event.timestamp === "number" ? event.timestamp : null
      if (ts != null) return ts
    }
    return null
  })()
  const ttftSeconds =
    startedAt != null && firstSseTimestamp != null
      ? Math.max(0, firstSseTimestamp - startedAt / 1000)
      : null

  const summary: TimelineSummary = {
    ptyEvents: ptyEvents.length,
    totalLinesChanged,
    totalBytes,
    sseEvents: sseEvents.length,
    warnings,
    ghostLines,
    ttftSeconds,
    spinnerHz,
    linesPerEvent,
    lineDiff,
    chaos: normalizeChaos(options?.chaosInfo ?? null),
    resizeStats: resizeStats ?? null,
  }

  const ndjson = timeline.map((entry) => JSON.stringify(entry)).join("\n")
  await fs.writeFile(timelinePath, ndjson.length > 0 ? `${ndjson}\n` : "", "utf8")
  await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8")

  return { timelinePath, summaryPath }
}
