import { describe, expect, test } from "bun:test"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { normalizeBridgeEvents } from "./event_adapter.ts"
import type { BridgeEvent } from "./bridge.ts"
import {
  appendContextBurstEvent,
  formatContextBurstBlock,
  formatContextBurstSummary,
  isContextCollectionEvent,
  type ContextBurstState,
} from "./context_burst.ts"

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

describe("context burst replay fixture", () => {
  test("groups consecutive context collection events and flushes at boundaries", () => {
    const bridgeEvents = loadFixtureEvents("context_burst_flush_smoke_v1.jsonl")
    const normalized = normalizeBridgeEvents(bridgeEvents)
    let burst: ContextBurstState | null = null
    const flushes: string[] = []

    for (const evt of normalized) {
      if (isContextCollectionEvent(evt)) {
        burst = appendContextBurstEvent(burst, evt)
        continue
      }
      if (burst) {
        flushes.push(formatContextBurstSummary(burst))
        burst = null
      }
    }
    if (burst) flushes.push(formatContextBurstSummary(burst))

    expect(flushes.length).toBe(2)
    expect(flushes[0]).toContain("[context] 4 ops")
    expect(flushes[0]).toContain("read_file×2")
    expect(flushes[0]).toContain("grep×2")
    expect(flushes[1]).toContain("[context] 2 ops")
    expect(flushes[1]).toContain("glob×2")

    let trailingBurst: ContextBurstState | null = null
    for (const evt of normalized) {
      if (isContextCollectionEvent(evt)) trailingBurst = appendContextBurstEvent(trailingBurst, evt)
    }
    if (trailingBurst) {
      const block = formatContextBurstBlock(trailingBurst)
      expect(block).toContain("[context]")
      expect(block).toContain("- glob")
    }
  })
})
