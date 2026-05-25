import fs from "node:fs"
import path from "node:path"

type JsonRecord = Record<string, unknown>

const writeSingletonDebugRecord = (envName: string, payload: JsonRecord): void => {
  const target = (process.env[envName] ?? "").trim()
  if (!target) return
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const qcBatchId = (process.env.BREADBOARD_QC_BATCH_ID ?? "").trim()
    const qcCaseId = (process.env.BREADBOARD_QC_CASE_ID ?? "").trim()
    const record = {
      ts: Date.now(),
      iso: new Date().toISOString(),
      ...(qcBatchId ? { qcBatchId } : {}),
      ...(qcCaseId ? { qcCaseId } : {}),
      ...payload,
    }
    fs.writeFileSync(target, `${JSON.stringify(record, null, 2)}\n`, "utf8")
  } catch {
    // Logging must never interfere with rendering paths.
  }
}

const appendDebugRecord = (envName: string, payload: JsonRecord): void => {
  const target = (process.env[envName] ?? "").trim()
  if (!target) return
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    const qcBatchId = (process.env.BREADBOARD_QC_BATCH_ID ?? "").trim()
    const qcCaseId = (process.env.BREADBOARD_QC_CASE_ID ?? "").trim()
    const record = {
      ts: Date.now(),
      iso: new Date().toISOString(),
      ...(qcBatchId ? { qcBatchId } : {}),
      ...(qcCaseId ? { qcCaseId } : {}),
      ...payload,
    }
    fs.appendFileSync(target, `${JSON.stringify(record)}\n`, "utf8")
  } catch {
    // Logging must never interfere with rendering paths.
  }
}

export const writeViewportResetDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_VIEWPORT_RESETS_FILE", payload)

export const writeSurfaceModelDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_SURFACE_MODEL_FILE", payload)

export const writeScrollbackFeedDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_SCROLLBACK_FEED_FILE", payload)

export const writeMarkdownMetricsDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_MARKDOWN_METRICS_FILE", payload)

export const writeRenderTimelineDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_RENDER_TIMELINE_FILE", payload)

export const writeManagedRegionBoundsDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE", payload)

export const writeComposerEventDebugRecord = (payload: JsonRecord): void =>
  appendDebugRecord("BREADBOARD_TUI_COMPOSER_EVENTS_FILE", payload)

export const writeAppStartAnchorDebugRecord = (payload: JsonRecord): void =>
  writeSingletonDebugRecord("BREADBOARD_TUI_APP_START_ANCHOR_FILE", payload)

export const writeScrollbackClauseVerdictsDebugRecord = (payload: JsonRecord): void =>
  writeSingletonDebugRecord("BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE", payload)
