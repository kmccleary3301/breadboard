#!/usr/bin/env node
import { appendFile, readFile } from "node:fs/promises"
import path from "node:path"
import process from "node:process"

const args = process.argv.slice(2)
const toNumber = (value) => {
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

const options = {
  summary: null,
  caseName: "case",
  ttftMs: toNumber(process.env.TTFT_BUDGET_MS),
  spinnerHz: toNumber(process.env.SPINNER_BUDGET_HZ),
  minSseEvents: toNumber(process.env.MIN_SSE_EVENTS),
  maxWarnings: toNumber(process.env.MAX_TIMELINE_WARNINGS),
  resizeEvents: toNumber(process.env.RESIZE_EVENT_BUDGET),
  resizeBurstMs: toNumber(process.env.RESIZE_BURST_BUDGET_MS),
  maxLinesPct: toNumber(process.env.MAX_LINES_CHANGED_PCT),
  p95LinesPct: toNumber(process.env.P95_LINES_CHANGED_PCT),
  maxGhostLines: toNumber(process.env.MAX_GHOST_LINES),
  maxFlickerEvents: toNumber(process.env.MAX_FLICKER_EVENTS),
  warningsFile: null,
}

for (let i = 0; i < args.length; i += 1) {
  const arg = args[i]
  switch (arg) {
    case "--summary":
      options.summary = args[++i]
      break
    case "--case":
      options.caseName = args[++i]
      break
    case "--ttft-ms":
      options.ttftMs = toNumber(args[++i])
      break
    case "--spinner-hz":
      options.spinnerHz = toNumber(args[++i])
      break
    case "--min-sse":
      options.minSseEvents = toNumber(args[++i])
      break
    case "--max-warnings":
      options.maxWarnings = toNumber(args[++i])
      break
    case "--resize-events":
      options.resizeEvents = toNumber(args[++i])
      break
    case "--resize-burst-ms":
      options.resizeBurstMs = toNumber(args[++i])
      break
    case "--max-lines-pct":
      options.maxLinesPct = toNumber(args[++i])
      break
    case "--p95-lines-pct":
      options.p95LinesPct = toNumber(args[++i])
      break
    case "--max-ghost-lines":
      options.maxGhostLines = toNumber(args[++i])
      break
    case "--max-flicker-events":
      options.maxFlickerEvents = toNumber(args[++i])
      break
    case "--warnings-file":
      options.warningsFile = args[++i]
      break
    default:
      break
  }
}

const usage = () => {
  console.error(
    "Usage: checkBudgets --summary <path> [--case <name>] [--ttft-ms <ms>] [--spinner-hz <hz>] [--min-sse <count>] [--max-warnings <count>] [--max-lines-pct <ratio>] [--p95-lines-pct <ratio>] [--max-ghost-lines <count>] [--max-flicker-events <count>]",
  )
  process.exit(2)
}

if (!options.summary) {
  usage()
}

const fail = (message) => {
  console.error(`[timeline-budget] ${message}`)
  process.exit(1)
}

const writeWarningsEntry = async (summaryPath, violations, metrics) => {
  if (!options.warningsFile) return
  const resolved = path.resolve(options.warningsFile)
  const entry = {
    case: options.caseName,
    summary: summaryPath,
    violations,
    metrics,
    budgets: {
      ttftMs: options.ttftMs,
      spinnerHz: options.spinnerHz,
      minSseEvents: options.minSseEvents,
      maxWarnings: options.maxWarnings,
      resizeEvents: options.resizeEvents,
      resizeBurstMs: options.resizeBurstMs,
      maxLinesPct: options.maxLinesPct,
      p95LinesPct: options.p95LinesPct,
      maxGhostLines: options.maxGhostLines,
      maxFlickerEvents: options.maxFlickerEvents,
    },
    timestamp: Date.now(),
  }
  await appendFile(resolved, `${JSON.stringify(entry)}\n`)
}

const main = async () => {
  const summaryPath = path.resolve(options.summary)
  let payload
  try {
    const contents = await readFile(summaryPath, "utf8")
    payload = JSON.parse(contents)
  } catch (error) {
    fail(`Unable to read summary ${summaryPath}: ${(error instanceof Error ? error.message : String(error))}`)
  }

  const violations = []
  const ttftSeconds = typeof payload.ttftSeconds === "number" ? payload.ttftSeconds : null
  if (options.ttftMs != null && options.ttftMs >= 0 && ttftSeconds != null) {
    const actualMs = ttftSeconds * 1000
    if (actualMs > options.ttftMs) {
      violations.push({
        metric: "ttftMs",
        actual: actualMs,
        budget: options.ttftMs,
        message: `${options.caseName}: TTFT ${actualMs.toFixed(0)}ms exceeds budget ${options.ttftMs.toFixed(0)}ms`,
      })
    }
  }

  const spinnerHz = typeof payload.spinnerHz === "number" ? payload.spinnerHz : null
  if (options.spinnerHz != null && options.spinnerHz >= 0 && spinnerHz != null && spinnerHz > options.spinnerHz) {
    violations.push({
      metric: "spinnerHz",
      actual: spinnerHz,
      budget: options.spinnerHz,
      message: `${options.caseName}: spinnerHz ${spinnerHz.toFixed(2)} exceeds budget ${options.spinnerHz.toFixed(2)}`,
    })
  }

  const sseEvents = typeof payload.sseEvents === "number" ? payload.sseEvents : null
  if (options.minSseEvents != null && sseEvents != null && sseEvents < options.minSseEvents) {
    violations.push({
      metric: "sseEvents",
      actual: sseEvents,
      budget: options.minSseEvents,
      message: `${options.caseName}: SSE events ${sseEvents} below minimum ${options.minSseEvents}`,
    })
  }

  const warnings = Array.isArray(payload.warnings) ? payload.warnings : []
  if (options.maxWarnings != null && warnings.length > options.maxWarnings) {
    violations.push({
      metric: "warnings",
      actual: warnings.length,
      budget: options.maxWarnings,
      message: `${options.caseName}: warnings ${warnings.length} exceed budget ${options.maxWarnings} (${warnings.join(", ")})`,
    })
  }

  const resizeStats = payload.resizeStats
  if (options.resizeEvents != null && resizeStats && typeof resizeStats.count === "number") {
    if (resizeStats.count > options.resizeEvents) {
      violations.push({
        metric: "resizeEvents",
        actual: resizeStats.count,
        budget: options.resizeEvents,
        message: `${options.caseName}: resize events ${resizeStats.count} exceed budget ${options.resizeEvents}`,
      })
    }
  }
  if (options.resizeBurstMs != null && resizeStats && typeof resizeStats.burstMs === "number") {
    if (resizeStats.burstMs > options.resizeBurstMs) {
      violations.push({
        metric: "resizeBurstMs",
        actual: resizeStats.burstMs,
        budget: options.resizeBurstMs,
        message: `${options.caseName}: resize burst ${resizeStats.burstMs}ms exceeds budget ${options.resizeBurstMs}ms`,
      })
    }
  }

  const lineDiff = payload.lineDiff || {}
  const maxLinesChangedPct = typeof lineDiff.maxLinesChangedPct === "number" ? lineDiff.maxLinesChangedPct : null
  if (options.maxLinesPct != null && maxLinesChangedPct != null && maxLinesChangedPct > options.maxLinesPct) {
    violations.push({
      metric: "maxLinesChangedPct",
      actual: maxLinesChangedPct,
      budget: options.maxLinesPct,
      message: `${options.caseName}: max line diff ${maxLinesChangedPct.toFixed(3)} exceeds budget ${options.maxLinesPct.toFixed(3)}`,
    })
  }
  const p95LinesChangedPct = typeof lineDiff.p95LinesChangedPct === "number" ? lineDiff.p95LinesChangedPct : null
  if (options.p95LinesPct != null && p95LinesChangedPct != null && p95LinesChangedPct > options.p95LinesPct) {
    violations.push({
      metric: "p95LinesChangedPct",
      actual: p95LinesChangedPct,
      budget: options.p95LinesPct,
      message: `${options.caseName}: p95 line diff ${p95LinesChangedPct.toFixed(3)} exceeds budget ${options.p95LinesPct.toFixed(3)}`,
    })
  }
  const ghostLines = typeof payload.ghostLines === "number" ? payload.ghostLines : null
  if (options.maxGhostLines != null && ghostLines != null && ghostLines > options.maxGhostLines) {
    violations.push({
      metric: "ghostLines",
      actual: ghostLines,
      budget: options.maxGhostLines,
      message: `${options.caseName}: ghost lines ${ghostLines} exceeds budget ${options.maxGhostLines}`,
    })
  }
  const flickerEvents =
    typeof lineDiff.flickerLineEvents === "number" ? lineDiff.flickerLineEvents : null
  if (options.maxFlickerEvents != null && flickerEvents != null && flickerEvents > options.maxFlickerEvents) {
    violations.push({
      metric: "flickerEvents",
      actual: flickerEvents,
      budget: options.maxFlickerEvents,
      message: `${options.caseName}: flicker events ${flickerEvents} exceeds budget ${options.maxFlickerEvents}`,
    })
  }

  const metricsSnapshot = {
    ttftMs: ttftSeconds != null ? ttftSeconds * 1000 : null,
    spinnerHz,
    sseEvents,
    warnings,
    resizeStats,
    lineDiff,
    ghostLines,
  }

  await writeWarningsEntry(summaryPath, violations, metricsSnapshot)

  if (violations.length > 0) {
    fail(violations[0].message)
  }
}

main().catch((error) => {
  fail(error instanceof Error ? error.message : String(error))
})
