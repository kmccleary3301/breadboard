import { describe, expect, it } from "vitest"
import { buildLaneDiagnosticsHeatmap } from "../diagnosticsHeatmap.js"

describe("buildLaneDiagnosticsHeatmap", () => {
  it("returns empty for empty input", () => {
    expect(buildLaneDiagnosticsHeatmap([], { nowMs: 10_000 })).toEqual([])
  })

  it("ranks lanes by activity/pressure and keeps deterministic order", () => {
    const rows = buildLaneDiagnosticsHeatmap(
      [
        { laneId: "lane-b", status: "running", updatedAt: 9_990 },
        { laneId: "lane-b", status: "failed", updatedAt: 9_995 },
        { laneId: "lane-a", status: "running", updatedAt: 9_980 },
        { laneId: "lane-a", status: "running", updatedAt: 9_981 },
      ],
      { nowMs: 10_000, barWidth: 8 },
    )
    expect(rows[0]?.laneId).toBe("lane-b")
    expect(rows[1]?.laneId).toBe("lane-a")
    expect(rows[0]?.score).toBeGreaterThan(rows[1]?.score ?? 0)
    expect(rows[0]?.bar.length).toBe(8)
  })

  it("derives intensity buckets from score bands", () => {
    const rows = buildLaneDiagnosticsHeatmap(
      [
        { laneId: "critical-lane", status: "failed", updatedAt: 10_000 },
        { laneId: "critical-lane", status: "failed", updatedAt: 10_000 },
        { laneId: "critical-lane", status: "failed", updatedAt: 10_000 },
        { laneId: "critical-lane", status: "running", updatedAt: 10_000 },
        { laneId: "critical-lane", status: "running", updatedAt: 10_000 },
        { laneId: "medium-lane", status: "running", updatedAt: 8_500 },
      ],
      { nowMs: 10_000 },
    )
    expect(rows[0]?.intensity).toBe("critical")
    const medium = rows.find((row) => row.laneId === "medium-lane")
    expect(medium?.intensity).toMatch(/low|medium|high|critical/)
  })

  it("applies row cap and lane labels", () => {
    const rows = buildLaneDiagnosticsHeatmap(
      [
        { laneId: "lane-a", status: "running", updatedAt: 5_000 },
        { laneId: "lane-b", status: "running", updatedAt: 5_000 },
        { laneId: "lane-c", status: "running", updatedAt: 5_000 },
      ],
      {
        nowMs: 6_000,
        maxRows: 2,
        laneLabelById: {
          "lane-a": "Alpha",
          "lane-b": "Bravo",
        },
      },
    )
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => row.laneLabel)).toContain("Alpha")
  })
})
