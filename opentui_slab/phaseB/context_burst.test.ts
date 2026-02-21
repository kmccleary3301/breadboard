import { describe, expect, test } from "bun:test"

import {
  appendContextBurstEvent,
  formatContextBurstBlock,
  formatContextBurstDetail,
  formatContextBurstSummary,
  isContextCollectionEvent,
  type ContextBurstState,
} from "./context_burst.ts"

describe("context burst helpers", () => {
  test("recognizes context-collection tool events only", () => {
    expect(isContextCollectionEvent({ type: "tool.call", toolClass: "context_collection" } as any)).toBe(true)
    expect(isContextCollectionEvent({ type: "tool.result", toolClass: "context_collection" } as any)).toBe(true)
    expect(isContextCollectionEvent({ type: "tool.call", toolClass: "default" } as any)).toBe(false)
    expect(isContextCollectionEvent({ type: "message.delta", toolClass: "context_collection" } as any)).toBe(false)
  })

  test("aggregates tool counts and failures", () => {
    let state: ContextBurstState | null = null
    state = appendContextBurstEvent(state, { toolName: "read_file", ok: true } as any)
    state = appendContextBurstEvent(state, { toolName: "read_file", ok: true } as any)
    state = appendContextBurstEvent(state, { toolName: "grep", ok: false } as any)

    expect(state.count).toBe(3)
    expect(state.failedCount).toBe(1)
    expect(state.toolCounts).toEqual({
      read_file: 2,
      grep: 1,
    })
    expect(state.entries.length).toBe(3)
    expect(state.entries[state.entries.length - 1]).toMatchObject({
      kind: "tool.result",
      toolName: "grep",
      ok: false,
    })

    const summary = formatContextBurstSummary(state)
    expect(summary).toContain("[context] 3 ops")
    expect(summary).toContain("read_file×2")
    expect(summary).toContain("grep×1")
    expect(summary).toContain("1 failed")

    const detail = formatContextBurstDetail(state)
    expect(detail).toContain("read_file ok")
    expect(detail).toContain("grep failed")

    const block = formatContextBurstBlock(state)
    expect(block).toContain("[context] 3 ops")
    expect(block).toContain("- read_file")
    expect(block).toContain("- grep (failed)")
  })
})
