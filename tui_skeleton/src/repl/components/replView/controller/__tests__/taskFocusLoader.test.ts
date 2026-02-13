import { describe, expect, it, vi } from "vitest"
import { buildTaskFocusArtifactCandidates, loadTaskFocusTail } from "../taskFocusLoader.js"

describe("taskFocusLoader", () => {
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
})
