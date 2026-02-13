import { describe, expect, it, vi } from "vitest"
import {
  __resetTaskFocusTailCacheForTests,
  buildTaskFocusArtifactCandidates,
  getTaskFocusTailCacheStats,
  loadTaskFocusTail,
} from "../taskFocusLoader.js"

describe("taskFocusLoader", () => {
  __resetTaskFocusTailCacheForTests()

  it("builds deterministic candidate list with explicit artifact path first", () => {
    const candidates = buildTaskFocusArtifactCandidates({
      id: "task-123",
      artifactPath: "/tmp/artifacts/task-123.jsonl",
    })
    expect(candidates).toEqual([
      "/tmp/artifacts/task-123.jsonl",
      ".breadboard/subagents/agent-task-123.jsonl",
      ".breadboard/subagents/task-123.jsonl",
      ".breadboard/subagents/task-123.json",
    ])
  })

  it("falls back to later candidate when initial artifact is missing/rotated", async () => {
    const readFile = vi.fn(async (path: string) => {
      if (path.includes("/tmp/stale.jsonl")) {
        throw new Error("ENOENT: stale artifact")
      }
      return {
        path,
        content: "line-a\nline-b",
        truncated: false,
      }
    })
    const result = await loadTaskFocusTail(
      {
        id: "task-rotated",
        artifactPath: "/tmp/stale.jsonl",
      },
      {
        rawMode: false,
        tailLines: 24,
        maxBytes: 40_000,
      },
      readFile as any,
    )

    expect(result.ok).toBe(true)
    expect(result.value?.path).toBe(".breadboard/subagents/agent-task-rotated.jsonl")
    expect(result.value?.lines).toEqual(["line-a", "line-b"])
    expect(readFile).toHaveBeenCalledTimes(2)
  })

  it("returns graceful failure when all candidates are unreadable", async () => {
    const readFile = vi.fn(async () => {
      throw new Error("EACCES: denied")
    })
    const result = await loadTaskFocusTail(
      {
        id: "task-fail",
      },
      {
        rawMode: true,
        tailLines: 24,
        maxBytes: 120_000,
      },
      readFile as any,
    )

    expect(result.ok).toBe(false)
    expect(result.failure?.error).toContain("EACCES")
    expect(readFile).toHaveBeenCalledTimes(3)
  })

  it("reuses cached tail results for identical load parameters", async () => {
    __resetTaskFocusTailCacheForTests()
    const readFile = vi.fn(async (path: string) => ({
      path,
      content: "line-1\nline-2",
      truncated: false,
    }))
    const first = await loadTaskFocusTail(
      {
        id: "task-cache",
      },
      {
        rawMode: false,
        tailLines: 24,
        maxBytes: 40_000,
        nowMs: 1_000,
      },
      readFile as any,
    )
    const second = await loadTaskFocusTail(
      {
        id: "task-cache",
      },
      {
        rawMode: false,
        tailLines: 24,
        maxBytes: 40_000,
        nowMs: 1_500,
      },
      readFile as any,
    )

    expect(first.ok).toBe(true)
    expect(second.ok).toBe(true)
    expect(second.value?.notice).toContain("[cache-hit]")
    expect(readFile).toHaveBeenCalledTimes(1)
    const stats = getTaskFocusTailCacheStats()
    expect(stats.hits).toBe(1)
  })

  it("expires cached entries after ttl and re-reads from source", async () => {
    __resetTaskFocusTailCacheForTests()
    const readFile = vi.fn(async (path: string) => ({
      path,
      content: "fresh-line",
      truncated: false,
    }))
    await loadTaskFocusTail(
      { id: "task-cache-expire" },
      { rawMode: false, tailLines: 24, maxBytes: 40_000, nowMs: 2_000, cacheTtlMs: 100 },
      readFile as any,
    )
    const next = await loadTaskFocusTail(
      { id: "task-cache-expire" },
      { rawMode: false, tailLines: 24, maxBytes: 40_000, nowMs: 2_201, cacheTtlMs: 100 },
      readFile as any,
    )

    expect(next.ok).toBe(true)
    expect(next.value?.notice).not.toContain("[cache-hit]")
    expect(readFile).toHaveBeenCalledTimes(2)
  })

  it("invalidates cache on raw/snippet mode change to avoid stale payload mode", async () => {
    __resetTaskFocusTailCacheForTests()
    const readFile = vi.fn(async (path: string, options: any) => ({
      path,
      content: options.mode === "cat" ? "raw-content" : "snippet-content",
      truncated: false,
    }))
    const snippet = await loadTaskFocusTail(
      { id: "task-cache-mode" },
      { rawMode: false, tailLines: 24, maxBytes: 40_000, nowMs: 3_000 },
      readFile as any,
    )
    const raw = await loadTaskFocusTail(
      { id: "task-cache-mode" },
      { rawMode: true, tailLines: 24, maxBytes: 40_000, nowMs: 3_100 },
      readFile as any,
    )

    expect(snippet.ok).toBe(true)
    expect(raw.ok).toBe(true)
    expect(snippet.value?.lines[0]).toBe("snippet-content")
    expect(raw.value?.lines[0]).toBe("raw-content")
    expect(readFile).toHaveBeenCalledTimes(2)
  })
})
