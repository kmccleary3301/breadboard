import { describe, expect, it } from "vitest"
import type { WorkGraphState } from "../../../../types.js"
import {
  buildSubagentStripSummary,
  createSubagentStripLifecycleState,
  evaluateSubagentStripChurn,
  reduceSubagentStripLifecycle,
  type SubagentStripSummary,
} from "../subagentStrip.js"

const createWorkGraph = (
  itemsById: WorkGraphState["itemsById"] = {},
  itemOrder: ReadonlyArray<string> = [],
): WorkGraphState => ({
  itemsById,
  itemOrder: [...itemOrder],
  lanesById: {},
  laneOrder: [],
  processedEventKeys: [],
  lastSeq: 0,
})

const summary = (running: number, completed = 0, failed = 0, blocked = 0, label = "task"): SubagentStripSummary => ({
  headline: `subagents ${running} run · ${completed} done · ${failed} fail · ${blocked} blocked`,
  detail: `${running > 0 ? "running" : "completed"} · ${label}`,
  tone: failed > 0 ? "error" : running > 0 ? "info" : "success",
  counts: { running, completed, failed, blocked },
})

describe("subagentStrip", () => {
  it("returns null summary when work graph is empty", () => {
    expect(buildSubagentStripSummary(createWorkGraph())).toBeNull()
  })

  it("derives aggregate counts, lead detail, and tone", () => {
    const itemsById: WorkGraphState["itemsById"] = {
      "task-1": {
        workId: "task-1",
        laneId: "lane-1",
        laneLabel: "lane-1",
        title: "Index docs",
        mode: "async",
        status: "failed",
        createdAt: 1,
        updatedAt: 2,
        parentWorkId: null,
        treePath: null,
        depth: null,
        artifactPaths: [],
        lastSafeExcerpt: null,
        steps: [],
        counters: { completed: 0, running: 0, failed: 0, total: 0 },
      },
      "task-2": {
        workId: "task-2",
        laneId: "lane-1",
        laneLabel: "lane-1",
        title: "Query DB",
        mode: "async",
        status: "running",
        createdAt: 1,
        updatedAt: 3,
        parentWorkId: null,
        treePath: null,
        depth: null,
        artifactPaths: [],
        lastSafeExcerpt: null,
        steps: [],
        counters: { completed: 0, running: 0, failed: 0, total: 0 },
      },
      "task-3": {
        workId: "task-3",
        laneId: "lane-2",
        laneLabel: "lane-2",
        title: "Summarize",
        mode: "sync",
        status: "completed",
        createdAt: 1,
        updatedAt: 1,
        parentWorkId: null,
        treePath: null,
        depth: null,
        artifactPaths: [],
        lastSafeExcerpt: null,
        steps: [],
        counters: { completed: 0, running: 0, failed: 0, total: 0 },
      },
      "task-4": {
        workId: "task-4",
        laneId: "lane-2",
        laneLabel: "lane-2",
        title: "Await approval",
        mode: "sync",
        status: "blocked",
        createdAt: 1,
        updatedAt: 1,
        parentWorkId: null,
        treePath: null,
        depth: null,
        artifactPaths: [],
        lastSafeExcerpt: null,
        steps: [],
        counters: { completed: 0, running: 0, failed: 0, total: 0 },
      },
    }
    const graph = createWorkGraph(itemsById, ["task-1", "task-2", "task-3", "task-4"])

    const strip = buildSubagentStripSummary(graph)
    expect(strip).toBeTruthy()
    expect(strip?.headline).toBe("subagents 1 run · 1 done · 1 fail · 1 blocked")
    expect(strip?.detail).toBe("failed · Index docs")
    expect(strip?.tone).toBe("error")
  })

  it("keeps strip visible through idle cooldown, then hides it", () => {
    let lifecycle = createSubagentStripLifecycleState()
    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: summary(1, 0, 0, 0, "first"),
      nowMs: 1_000,
      idleCooldownMs: 1_200,
      minUpdateMs: 0,
    })
    expect(lifecycle.rendered?.counts.running).toBe(1)

    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: null,
      nowMs: 1_900,
      idleCooldownMs: 1_200,
      minUpdateMs: 0,
    })
    expect(lifecycle.rendered).not.toBeNull()

    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: null,
      nowMs: 2_300,
      idleCooldownMs: 1_200,
      minUpdateMs: 0,
    })
    expect(lifecycle.rendered).toBeNull()
  })

  it("throttles summary churn inside min update window", () => {
    let lifecycle = createSubagentStripLifecycleState()
    const first = summary(1, 0, 0, 0, "first")
    const second = summary(1, 0, 0, 0, "second")

    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: first,
      nowMs: 100,
      idleCooldownMs: 500,
      minUpdateMs: 100,
    })
    expect(lifecycle.rendered?.detail).toContain("first")

    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: second,
      nowMs: 150,
      idleCooldownMs: 500,
      minUpdateMs: 100,
    })
    expect(lifecycle.rendered?.detail).toContain("first")

    lifecycle = reduceSubagentStripLifecycle(lifecycle, {
      summary: second,
      nowMs: 220,
      idleCooldownMs: 500,
      minUpdateMs: 100,
    })
    expect(lifecycle.rendered?.detail).toContain("second")
  })

  it("computes stable churn ratio for repeated summary samples", () => {
    const metrics = evaluateSubagentStripChurn([
      summary(1, 0, 0, 0, "same"),
      summary(1, 0, 0, 0, "same"),
      summary(1, 0, 0, 0, "same"),
    ])
    expect(metrics.samples).toBe(3)
    expect(metrics.transitions).toBe(0)
    expect(metrics.ratio).toBe(0)
  })

  it("counts transitions when strip samples change state", () => {
    const metrics = evaluateSubagentStripChurn([
      summary(1, 0, 0, 0, "first"),
      summary(1, 0, 0, 0, "first"),
      summary(0, 1, 0, 0, "done"),
      null,
    ])
    expect(metrics.samples).toBe(4)
    expect(metrics.transitions).toBe(2)
    expect(metrics.ratio).toBeCloseTo(2 / 3, 5)
  })
})
