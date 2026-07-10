import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync } from "node:fs"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { buildEmulatorResizeRenderCorrelationReport } from "../tools/reports/emulatorResizeRenderCorrelation.ts"

describe("emulatorResizeRenderCorrelation", () => {
  it("classifies degraded frames with warm landing + overshoot as a render-side resize root cause", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-render-correlation-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.mkdir(path.join(dir, "observer_runtime"), { recursive: true })
      await fs.writeFile(
        path.join(dir, "emulator_frames.ndjson"),
        `${JSON.stringify({ label: "streaming-churn-2", timestamp: 1_000 })}\n${JSON.stringify({ label: "settled-medium", timestamp: 1_400 })}\n`,
        "utf8",
      )
      await fs.writeFile(path.join(dir, "observer_text", "streaming-churn-2.txt"), "landing only\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-medium.txt"), "❯\n- item-20\n", "utf8")
      await fs.writeFile(
        path.join(dir, "render_timeline.ndjson"),
        [
          { ts: 995, event: "render_geometry", rowCount: 24, contentWidth: 96, bodyBudgetRows: 13, managedBodyRows: 20, managedViewportRowsAboveCursor: 21, landingVariant: "split" },
          { ts: 1002, event: "render_commit", pendingResponse: true, warmLandingVisible: true, landingRetired: false, activeWindowUsedLines: 18, transcriptBudgetRows: 9, activeWindowOvershoot: true, appendLandingToFeed: false, transcriptTailCount: 1 },
          { ts: 1390, event: "render_commit", pendingResponse: false, warmLandingVisible: false, landingRetired: true, activeWindowUsedLines: 20, transcriptBudgetRows: 27, activeWindowOvershoot: false, appendLandingToFeed: true, transcriptTailCount: 0 },
        ].map((record) => JSON.stringify(record)).join("\n") + "\n",
        "utf8",
      )
      await fs.writeFile(path.join(dir, "surface_model.ndjson"), "", "utf8")
      await fs.writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 100, event: "managed_viewport_reset_key" }) + "\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_runtime", "wezterm-events.ndjson"), "event=window-resized\n", "utf8")

      const report = await buildEmulatorResizeRenderCorrelationReport(dir)
      expect(report.likelyRootCause).toBe("landing_budget_overshoot_during_active_resize")
      expect(report.degradedFrames).toEqual(["streaming-churn-2"])
      expect(report.frameCorrelations[0]?.nearestCommit?.activeWindowOvershoot).toBe(true)
      expect(report.frameCorrelations[0]?.nearestCommit?.warmLandingVisible).toBe(true)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("classifies immediate degraded frames with healthy nearest commit state as observer capture transients", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-render-correlation-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.mkdir(path.join(dir, "observer_runtime"), { recursive: true })
      await fs.writeFile(
        path.join(dir, "emulator_frames.ndjson"),
        `${JSON.stringify({ label: "immediate-small", timestamp: 2_000 })}\n${JSON.stringify({ label: "settled-small", timestamp: 2_400 })}\n`,
        "utf8",
      )
      await fs.writeFile(path.join(dir, "observer_text", "immediate-small.txt"), "\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-small.txt"), "❯\n- item-20\n", "utf8")
      await fs.writeFile(
        path.join(dir, "render_timeline.ndjson"),
        [
          { ts: 1991, event: "render_geometry", rowCount: 22, contentWidth: 88, bodyBudgetRows: 13, managedBodyRows: 14, managedViewportRowsAboveCursor: 15, landingVariant: "split" },
          { ts: 2009, event: "render_commit", pendingResponse: true, warmLandingVisible: true, landingRetired: false, activeWindowUsedLines: 8, transcriptBudgetRows: 9, activeWindowOvershoot: false, appendLandingToFeed: false, transcriptTailCount: 1 },
          { ts: 2390, event: "render_commit", pendingResponse: false, warmLandingVisible: false, landingRetired: true, activeWindowUsedLines: 20, transcriptBudgetRows: 27, activeWindowOvershoot: false, appendLandingToFeed: true, transcriptTailCount: 0 },
        ].map((record) => JSON.stringify(record)).join("\n") + "\n",
        "utf8",
      )
      await fs.writeFile(path.join(dir, "surface_model.ndjson"), "", "utf8")
      await fs.writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ ts: 100, event: "managed_viewport_reset_key" }) + "\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_runtime", "wezterm-events.ndjson"), "event=window-resized\n", "utf8")

      const report = await buildEmulatorResizeRenderCorrelationReport(dir)
      expect(report.likelyRootCause).toBe("observer_capture_transient_after_resize")
      expect(report.degradedFrames).toEqual(["immediate-small"])
      expect(report.frameCorrelations[0]?.nearestCommit?.activeWindowOvershoot).toBe(false)
      expect(report.frameCorrelations[0]?.nearestCommit?.transcriptTailCount).toBe(1)
      expect(report.delayedDegradedFrames).toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
