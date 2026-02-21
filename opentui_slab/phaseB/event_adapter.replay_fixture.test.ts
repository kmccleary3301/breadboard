import { describe, expect, test } from "bun:test"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { normalizeBridgeEvents } from "./event_adapter.ts"
import type { BridgeEvent } from "./bridge.ts"

type FixtureRow = {
  type: string
  payload?: Record<string, unknown>
}

const loadFixtureEvents = (filename: string): BridgeEvent[] => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const fixturePath = path.resolve(here, "../../config/cli_bridge_replays/phase4", filename)
  const lines = fs.readFileSync(fixturePath, "utf8").split(/\r?\n/)
  const rows: FixtureRow[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith("#")) continue
    const parsed = JSON.parse(trimmed) as FixtureRow
    if (!parsed || typeof parsed.type !== "string") continue
    rows.push(parsed)
  }
  return rows.map((row, idx) => ({
    id: String(idx + 1),
    seq: idx + 1,
    type: row.type,
    payload: row.payload ?? {},
    timestamp_ms: idx + 1,
    session_id: "fixture-session",
    turn: 1,
  }))
}

const countByType = (events: ReadonlyArray<{ type: string }>): Record<string, number> => {
  const counts: Record<string, number> = {}
  for (const evt of events) {
    counts[evt.type] = (counts[evt.type] ?? 0) + 1
  }
  return counts
}

describe("event adapter replay fixtures", () => {
  test("subagents strip churn fixture maps task updates", () => {
    const bridgeEvents = loadFixtureEvents("subagents_strip_churn_smoke_v1.jsonl")
    const normalized = normalizeBridgeEvents(bridgeEvents)
    const counts = countByType(normalized)
    expect(normalized.length).toBeGreaterThan(0)
    expect((counts["task.update"] ?? 0)).toBeGreaterThan(4)
    expect((counts["unknown"] ?? 0)).toBeLessThan(normalized.length)
  })

  test("thinking lifecycle fixture maps assistant deltas", () => {
    const bridgeEvents = loadFixtureEvents("thinking_lifecycle_expiration_smoke_v1.jsonl")
    const normalized = normalizeBridgeEvents(bridgeEvents)
    const counts = countByType(normalized)
    expect(normalized.length).toBeGreaterThan(0)
    expect((counts["message.delta"] ?? 0)).toBeGreaterThan(0)
    expect((counts["unknown"] ?? 0)).toBeLessThan(normalized.length)
  })

  test("resize overlay fixture maps warnings and task updates", () => {
    const bridgeEvents = loadFixtureEvents("resize_overlay_interaction_smoke_v1.jsonl")
    const normalized = normalizeBridgeEvents(bridgeEvents)
    const counts = countByType(normalized)
    expect(normalized.length).toBeGreaterThan(0)
    expect((counts["warning"] ?? 0)).toBeGreaterThan(0)
    expect((counts["task.update"] ?? 0)).toBeGreaterThan(0)
    expect((counts["message.delta"] ?? 0) + (counts["message.final"] ?? 0)).toBeGreaterThan(0)
  })

  test("context burst fixture classifies context collection tools", () => {
    const bridgeEvents = loadFixtureEvents("context_burst_flush_smoke_v1.jsonl")
    const normalized = normalizeBridgeEvents(bridgeEvents)
    const contextTools = normalized.filter(
      (evt) =>
        (evt.type === "tool.call" || evt.type === "tool.result") &&
        evt.toolClass === "context_collection",
    )
    const defaultTools = normalized.filter(
      (evt) =>
        (evt.type === "tool.call" || evt.type === "tool.result") &&
        evt.toolClass === "default",
    )
    expect(contextTools.length).toBeGreaterThan(0)
    expect(defaultTools.length).toBeGreaterThan(0)
  })
})
