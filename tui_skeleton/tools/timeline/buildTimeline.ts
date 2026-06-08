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
  readonly firstEventOffsetMs?: number | null
  readonly lastEventOffsetMs?: number | null
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
  readonly markdownMetricsPath?: string | null
  readonly chaosInfo?: Record<string, unknown> | null
}

interface ManagedRegionBoundsEvent {
  readonly ts: number
  readonly rowCount: number
  readonly managedBodyRows: number
  readonly composerRowsAboveCursor: number
  readonly landingRowCount?: number
  readonly warmLandingVisible?: boolean
}

interface StaticFeedAppendEvent {
  readonly ts: number
  readonly appendedCount: number
}

interface MarkdownPaintSummary {
  readonly schemaVersion: 1
  readonly phase: "p12-instrumentation-baseline"
  readonly correctnessAnomalies: number | null
  readonly ptyEvents: number
  readonly sseEvents: number
  readonly totalLinesChanged: number
  readonly totalBytes: number
  readonly resizeBurstFrames: number
  readonly nonResizeFrames: number
  readonly meanRowsChangedPctNonResize: number | null
  readonly p95RowsChangedPctNonResize: number | null
  readonly p99RowsChangedPctNonResize: number | null
  readonly maxRowsChangedPctNonResize: number | null
  readonly flickerLineEvents: number | null
  readonly broadClearCount: number
  readonly unknownAttributionRows: number
  readonly blockCount: number | null
  readonly changedBlockCount: number | null
  readonly finalizedBlockCount: number | null
  readonly cacheHitRate: number | null
  readonly cacheMissCount: number | null
  readonly staticFeedAppendCount: number | null
  readonly staticChunkDuplicateCount: number | null
  readonly frozenBlockMutationCount: number | null
  readonly activeFrozenOverlapCount: number | null
  readonly maxHotTailRows: number | null
  readonly taskTreePreviewOccurrences: number | null
  readonly notes: string[]
}

interface BlockOwnershipSummary {
  readonly schemaVersion: 1
  readonly phase: "p12-instrumentation-baseline"
  readonly semanticMessageCount: number | null
  readonly blockCount: number | null
  readonly hotBlockCount: number | null
  readonly heldStableBlockCount: number | null
  readonly frozenStaticBlockCount: number | null
  readonly staticFeedAppendCount: number | null
  readonly staticChunkDuplicateCount: number | null
  readonly frozenBlockMutationCount: number | null
  readonly activeFrozenOverlapCount: number | null
  readonly notes: string[]
}

interface PaintAttributionSummary {
  readonly schemaVersion: 1
  readonly phase: "p12-instrumentation-baseline"
  readonly totalRowsChanged: number
  readonly hotTailRowsChanged: number | null
  readonly composerRowsChanged: number | null
  readonly footerRowsChanged: number | null
  readonly landingRowsChanged: number | null
  readonly overlayRowsChanged: number | null
  readonly taskTreeRowsChanged: number | null
  readonly frozenChunkRowsChanged: number
  readonly unknownRowsChanged: number
  readonly unknownRowsChangedPct: number | null
  readonly notes: string[]
}

export const buildTimelineArtifacts = async (caseDir: string, options?: TimelineOptions) => {
  const metadataPath = options?.metadataPath ?? path.join(caseDir, "pty_metadata.json")
  const gridDeltasPath = options?.gridDeltaPath ?? path.join(caseDir, "grid_deltas.ndjson")
  const ssePath = options?.ssePath ?? path.join(caseDir, "sse_events.txt")
  const anomaliesPath = options?.anomaliesPath ?? path.join(caseDir, "anomalies.json")
  const markdownMetricsInputPath = options?.markdownMetricsPath ?? path.join(caseDir, "markdown_metrics.ndjson")
  const managedRegionBoundsPath = path.join(caseDir, "managed_region_bounds.ndjson")
  const scrollbackFeedPath = path.join(caseDir, "scrollback_feed.ndjson")
  const timelinePath = path.join(caseDir, "timeline.ndjson")
  const summaryPath = path.join(caseDir, "timeline_summary.json")
  const markdownPaintPath = path.join(caseDir, "markdown_paint.ndjson")
  const markdownPaintSummaryPath = path.join(caseDir, "markdown_paint_summary.json")
  const blockOwnershipSummaryPath = path.join(caseDir, "block_ownership_summary.json")
  const paintAttributionSummaryPath = path.join(caseDir, "paint_attribution_summary.json")

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
    const firstEventOffsetMs =
      value.firstEventOffsetMs == null ? null : Number(value.firstEventOffsetMs)
    const lastEventOffsetMs =
      value.lastEventOffsetMs == null ? null : Number(value.lastEventOffsetMs)
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
      firstEventOffsetMs: Number.isFinite(firstEventOffsetMs) ? firstEventOffsetMs : null,
      lastEventOffsetMs: Number.isFinite(lastEventOffsetMs) ? lastEventOffsetMs : null,
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
  const markdownMetricEvents = await readLines(markdownMetricsInputPath).then((lines) =>
    lines.flatMap((line) => {
      if (!line.trim()) return []
      try {
        return [JSON.parse(line) as Record<string, unknown>]
      } catch {
        return []
      }
    }),
  )
  const managedRegionBoundsEvents = await readLines(managedRegionBoundsPath).then((lines) =>
    lines.flatMap((line): ManagedRegionBoundsEvent[] => {
      if (!line.trim()) return []
      try {
        const event = JSON.parse(line) as Record<string, unknown>
        const ts = typeof event.ts === "number" ? event.ts : null
        const rowCount = typeof event.rowCount === "number" ? event.rowCount : null
        const managedBodyRows = typeof event.managedBodyRows === "number" ? event.managedBodyRows : null
        const composerRowsAboveCursor = typeof event.composerRowsAboveCursor === "number" ? event.composerRowsAboveCursor : null
        if (ts == null || rowCount == null || managedBodyRows == null || composerRowsAboveCursor == null) return []
        return [{
          ts,
          rowCount,
          managedBodyRows,
          composerRowsAboveCursor,
          landingRowCount: typeof event.landingRowCount === "number" ? event.landingRowCount : undefined,
          warmLandingVisible: event.warmLandingVisible === true,
        }]
      } catch {
        return []
      }
    }),
  )
  const staticFeedAppendEvents = await readLines(scrollbackFeedPath).then((lines) =>
    lines.flatMap((line): StaticFeedAppendEvent[] => {
      if (!line.trim()) return []
      try {
        const event = JSON.parse(line) as Record<string, unknown>
        if (event.event !== "append_markdown_static_chunks") return []
        const ts = typeof event.ts === "number" ? event.ts : null
        if (ts == null) return []
        return [{
          ts,
          appendedCount: typeof event.appendedCount === "number" ? event.appendedCount : 0,
        }]
      } catch {
        return []
      }
    }),
  )
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

  const isResizeFrame = (event: { timestamp: number | null }): boolean => {
    if (!resizeStats || !startedAt || event.timestamp == null) return false
    if (resizeStats.firstEventOffsetMs == null || resizeStats.lastEventOffsetMs == null) return false
    const offset = event.timestamp - startedAt
    return offset >= resizeStats.firstEventOffsetMs && offset <= resizeStats.lastEventOffsetMs
  }
  const nonResizePcts =
    gridRows && gridRows > 0
      ? ptyEvents
          .filter((event) => !isResizeFrame(event))
          .map((event) => event.changedLines.length / gridRows)
      : []
  const resizeBurstFrames = ptyEvents.filter(isResizeFrame).length
  const broadClearCount =
    gridRows && gridRows > 0
      ? ptyEvents.filter((event) => event.changedLines.length / gridRows >= 0.9).length
      : 0
  const mean = (values: readonly number[]) =>
    values.length > 0 ? values.reduce((acc, value) => acc + value, 0) / values.length : null
  const max = (values: readonly number[]) => (values.length > 0 ? Math.max(...values) : null)
  const latestBoundsFor = (timestamp: number | null): ManagedRegionBoundsEvent | null => {
    if (timestamp == null || managedRegionBoundsEvents.length === 0) return null
    let best: ManagedRegionBoundsEvent | null = null
    for (const event of managedRegionBoundsEvents) {
      if (event.ts > timestamp) break
      best = event
    }
    return best
  }
  const hasStaticAppendNear = (timestamp: number | null): boolean => {
    if (timestamp == null) return false
    return staticFeedAppendEvents.some((event) => Math.abs(event.ts - timestamp) <= 50)
  }
  const classifyChangedRows = (event: { timestamp: number | null; changedLines: number[] }) => {
    const attribution = {
      hotTail: 0,
      composer: 0,
      footer: 0,
      landing: 0,
      overlay: 0,
      taskTree: 0,
      frozenChunk: 0,
      unknown: 0,
    }
    const bounds = latestBoundsFor(event.timestamp)
    const staticAppend = hasStaticAppendNear(event.timestamp)
    if (!bounds) {
      const firstBounds = managedRegionBoundsEvents[0]
      if (event.timestamp != null && firstBounds && firstBounds.ts - event.timestamp >= 0 && firstBounds.ts - event.timestamp <= 25) {
        attribution.frozenChunk = event.changedLines.length
      } else {
        attribution.unknown = event.changedLines.length
      }
      return attribution
    }
    const rowCount = bounds.rowCount
    const composerStart = Math.max(0, rowCount - bounds.composerRowsAboveCursor)
    const managedStart = Math.max(0, composerStart - bounds.managedBodyRows)
    const managedEnd = Math.max(managedStart - 1, composerStart - 1)
    const landingStart = Math.max(0, managedStart - (bounds.warmLandingVisible ? bounds.landingRowCount ?? 0 : 0))
    for (const line of event.changedLines) {
      if (line >= rowCount) {
        attribution.frozenChunk += 1
      } else if (line >= composerStart && line < rowCount) {
        // The last two managed rows are status/footer in current preserved-scrollback layout.
        if (line >= Math.max(composerStart, rowCount - 2)) attribution.footer += 1
        else attribution.composer += 1
      } else if (staticAppend) {
        attribution.frozenChunk += 1
      } else if (line >= managedStart && line <= managedEnd) {
        attribution.hotTail += 1
      } else if (bounds.warmLandingVisible && line >= landingStart && line < managedStart) {
        attribution.landing += 1
      } else if (line < managedStart) {
        attribution.frozenChunk += 1
      } else {
        attribution.unknown += 1
      }
    }
    return attribution
  }
  const markdownPaintEvents = ptyEvents.map((event, index) => {
    const rowsChanged = event.changedLines.length
    const rowsChangedPct = gridRows && gridRows > 0 ? rowsChanged / gridRows : null
    const attribution = classifyChangedRows(event)
    return {
      event: "paint_budget",
      schemaVersion: 1,
      frameId: index,
      timestamp: event.timestamp,
      cause: isResizeFrame(event) ? "resize" : "unknown",
      rowsChanged,
      rowsChangedPct,
      attribution: {
        hotTail: attribution.hotTail,
        composer: attribution.composer,
        footer: attribution.footer,
        landing: attribution.landing,
        overlay: attribution.overlay,
        taskTree: attribution.taskTree,
        frozenChunk: attribution.frozenChunk,
        unknown: attribution.unknown,
      },
    }
  })
  const attributionTotals = markdownPaintEvents.reduce(
    (acc, event) => {
      const attribution = event.attribution
      acc.hotTail += attribution.hotTail ?? 0
      acc.composer += attribution.composer ?? 0
      acc.footer += attribution.footer ?? 0
      acc.landing += attribution.landing ?? 0
      acc.overlay += attribution.overlay ?? 0
      acc.taskTree += attribution.taskTree ?? 0
      acc.frozenChunk += attribution.frozenChunk ?? 0
      acc.unknown += attribution.unknown ?? 0
      return acc
    },
    { hotTail: 0, composer: 0, footer: 0, landing: 0, overlay: 0, taskTree: 0, frozenChunk: 0, unknown: 0 },
  )
  const documentUpdates = markdownMetricEvents.filter((event) => event.event === "markdown_document_update")
  const latestDocumentByMessage = new Map<string, Record<string, unknown>>()
  for (const event of documentUpdates) {
    const messageId = typeof event.messageId === "string" ? event.messageId : null
    if (messageId) latestDocumentByMessage.set(messageId, event)
  }
  const latestDocuments = [...latestDocumentByMessage.values()]
  const cacheEvents = markdownMetricEvents.filter((event) => event.event === "markdown_render_cache")
  const cacheHits = cacheEvents.filter((event) => event.result === "hit").length
  const cacheMisses = cacheEvents.filter((event) => event.result === "miss").length
  const cacheTotal = cacheHits + cacheMisses
  const staticChunkEvents = markdownMetricEvents.filter((event) => event.event === "markdown_chunk_promote" || event.event === "static_chunk_append")
  const staticChunkIds = staticChunkEvents
    .map((event) => (typeof event.chunkId === "string" ? event.chunkId : typeof event.staticFeedItemId === "string" ? event.staticFeedItemId : null))
    .filter((value): value is string => Boolean(value))
  const staticChunkDuplicateCount = staticChunkIds.length - new Set(staticChunkIds).size
  const frozenBlockMutationCount = markdownMetricEvents.filter((event) => event.event === "markdown_frozen_mutation_attempt").length
  const activeFrozenOverlapCount = markdownMetricEvents.filter((event) => event.event === "markdown_active_frozen_overlap").length
  const hotTailEvents = markdownMetricEvents.filter((event) => event.event === "markdown_hot_tail")
  const hotTailRows = hotTailEvents
    .filter((event) => event.event === "markdown_hot_tail")
    .map((event) => (typeof event.rowCount === "number" && Number.isFinite(event.rowCount) ? event.rowCount : null))
    .filter((value): value is number => value != null)
  const hotBlockIds = new Set<string>()
  for (const event of hotTailEvents) {
    if (Array.isArray(event.blockIds)) {
      for (const blockId of event.blockIds) {
        if (typeof blockId === "string") hotBlockIds.add(blockId)
      }
    }
  }
  const frozenBlockIds = new Set<string>()
  for (const event of staticChunkEvents) {
    if (Array.isArray(event.blockIds)) {
      for (const blockId of event.blockIds) {
        if (typeof blockId === "string") frozenBlockIds.add(blockId)
      }
    }
  }
  const blockCounts = latestDocuments
    .map((event) => (typeof event.blockCount === "number" && Number.isFinite(event.blockCount) ? event.blockCount : 0))
  const changedBlockCounts = documentUpdates.map((event) => Array.isArray(event.changedBlockIds) ? event.changedBlockIds.length : 0)
  const finalizedBlockCounts = latestDocuments.map((event) => Array.isArray(event.finalizedBlockIds) ? event.finalizedBlockIds.length : 0)
  const hasMarkdownRuntimeMetrics = markdownMetricEvents.length > 0
  const markdownPaintSummary: MarkdownPaintSummary = {
    schemaVersion: 1,
    phase: "p12-instrumentation-baseline",
    correctnessAnomalies: Array.isArray(anomalies) ? anomalies.length : null,
    ptyEvents: ptyEvents.length,
    sseEvents: sseEvents.length,
    totalLinesChanged,
    totalBytes,
    resizeBurstFrames,
    nonResizeFrames: nonResizePcts.length,
    meanRowsChangedPctNonResize: mean(nonResizePcts),
    p95RowsChangedPctNonResize: buildPercentile(nonResizePcts, 0.95),
    p99RowsChangedPctNonResize: buildPercentile(nonResizePcts, 0.99),
    maxRowsChangedPctNonResize: max(nonResizePcts),
    flickerLineEvents: lineDiff.flickerLineEvents,
    broadClearCount,
    unknownAttributionRows: attributionTotals.unknown,
    blockCount: hasMarkdownRuntimeMetrics ? blockCounts.reduce((acc, value) => acc + value, 0) : null,
    changedBlockCount: hasMarkdownRuntimeMetrics ? changedBlockCounts.reduce((acc, value) => acc + value, 0) : null,
    finalizedBlockCount: hasMarkdownRuntimeMetrics ? finalizedBlockCounts.reduce((acc, value) => acc + value, 0) : null,
    cacheHitRate: cacheTotal > 0 ? cacheHits / cacheTotal : null,
    cacheMissCount: cacheTotal > 0 ? cacheMisses : null,
    staticFeedAppendCount: hasMarkdownRuntimeMetrics ? staticChunkEvents.length : null,
    staticChunkDuplicateCount: hasMarkdownRuntimeMetrics ? staticChunkDuplicateCount : null,
    frozenBlockMutationCount: hasMarkdownRuntimeMetrics ? frozenBlockMutationCount : null,
    activeFrozenOverlapCount: hasMarkdownRuntimeMetrics ? activeFrozenOverlapCount : null,
    maxHotTailRows: hotTailRows.length > 0 ? Math.max(...hotTailRows) : null,
    taskTreePreviewOccurrences: null,
    notes: [
      "Phase 1 baseline derives paint metrics from grid deltas only.",
      hasMarkdownRuntimeMetrics
        ? "Runtime markdown metrics were consumed from markdown_metrics.ndjson."
        : "Block, cache, hot-tail, and frozen ownership metrics are null until runtime instrumentation is added.",
      managedRegionBoundsEvents.length > 0
        ? "Managed-region bounds and static-feed append records were used for conservative row attribution."
        : "All changed rows are attributed to unknown until surface ownership ranges are emitted.",
    ],
  }
  const blockOwnershipSummary: BlockOwnershipSummary = {
    schemaVersion: 1,
    phase: "p12-instrumentation-baseline",
    semanticMessageCount: hasMarkdownRuntimeMetrics ? latestDocuments.length : null,
    blockCount: hasMarkdownRuntimeMetrics ? blockCounts.reduce((acc, value) => acc + value, 0) : null,
    hotBlockCount: hotTailEvents.length > 0 ? hotBlockIds.size : null,
    heldStableBlockCount: null,
    frozenStaticBlockCount: staticChunkEvents.length > 0 ? frozenBlockIds.size : null,
    staticFeedAppendCount: hasMarkdownRuntimeMetrics ? staticChunkEvents.length : null,
    staticChunkDuplicateCount: hasMarkdownRuntimeMetrics ? staticChunkDuplicateCount : null,
    frozenBlockMutationCount: hasMarkdownRuntimeMetrics ? frozenBlockMutationCount : null,
    activeFrozenOverlapCount: hasMarkdownRuntimeMetrics ? activeFrozenOverlapCount : null,
    notes: [
      hasMarkdownRuntimeMetrics
        ? "Runtime markdown document/cache metrics were consumed. Hot/held/frozen ownership states require Phase 4 fragment instrumentation."
        : "Runtime block ownership events are not emitted yet.",
      hasMarkdownRuntimeMetrics
        ? "Null hot/held/frozen state counts are intentional until fragment ownership exists."
        : "Null values are intentional Phase 1 placeholders, not passing ownership proof.",
    ],
  }
  const paintAttributionSummary: PaintAttributionSummary = {
    schemaVersion: 1,
    phase: "p12-instrumentation-baseline",
    totalRowsChanged: totalLinesChanged,
    hotTailRowsChanged: attributionTotals.hotTail,
    composerRowsChanged: attributionTotals.composer,
    footerRowsChanged: attributionTotals.footer,
    landingRowsChanged: attributionTotals.landing,
    overlayRowsChanged: attributionTotals.overlay,
    taskTreeRowsChanged: attributionTotals.taskTree,
    frozenChunkRowsChanged: attributionTotals.frozenChunk,
    unknownRowsChanged: attributionTotals.unknown,
    unknownRowsChangedPct: totalLinesChanged > 0 ? attributionTotals.unknown / totalLinesChanged : 0,
    notes: [
      managedRegionBoundsEvents.length > 0
        ? "Conservative attribution uses managed-region bounds, composer rows, and static markdown append timing."
        : "Timeline-level baseline cannot attribute rows to surfaces yet.",
      "Unknown rows are rows outside the managed active/composer/footer ranges and not temporally associated with static markdown appends.",
    ],
  }

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
      ? Math.max(0, (firstSseTimestamp - startedAt) / 1000)
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
  const markdownPaintNdjson = markdownPaintEvents.map((entry) => JSON.stringify(entry)).join("\n")
  await fs.writeFile(timelinePath, ndjson.length > 0 ? `${ndjson}\n` : "", "utf8")
  await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8")
  await fs.writeFile(markdownPaintPath, markdownPaintNdjson.length > 0 ? `${markdownPaintNdjson}\n` : "", "utf8")
  await fs.writeFile(markdownPaintSummaryPath, `${JSON.stringify(markdownPaintSummary, null, 2)}\n`, "utf8")
  await fs.writeFile(blockOwnershipSummaryPath, `${JSON.stringify(blockOwnershipSummary, null, 2)}\n`, "utf8")
  await fs.writeFile(paintAttributionSummaryPath, `${JSON.stringify(paintAttributionSummary, null, 2)}\n`, "utf8")

  return {
    timelinePath,
    summaryPath,
    markdownPaintPath,
    markdownPaintSummaryPath,
    blockOwnershipSummaryPath,
    paintAttributionSummaryPath,
  }
}
