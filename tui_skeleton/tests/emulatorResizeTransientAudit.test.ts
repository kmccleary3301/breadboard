import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync } from "node:fs"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { buildEmulatorResizeTransientAudit } from "../tools/reports/emulatorResizeTransientAudit.ts"

describe("emulatorResizeTransientAudit", () => {
  it("classifies immediate-only degradation separately from settled recovery", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-resize-transient-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.writeFile(path.join(dir, "observer_text", "immediate-small.txt"), "- item-01\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-small.txt"), "❯\n- item-01\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "immediate-medium.txt"), "❯\n- item-02\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-medium.txt"), "❯\n- item-03\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-history.txt"), "- item-20\n", "utf8")
      await fs.writeFile(
        path.join(dir, "viewport_resets.ndjson"),
        JSON.stringify({ event: "managed_viewport_reset_key" }) + "\n",
        "utf8",
      )
      await fs.writeFile(
        path.join(dir, "surface_model.ndjson"),
        JSON.stringify({ pendingResponse: false, landingVariant: "board", activeWindowHiddenCount: 0, activeWindowTruncated: false }) + "\n",
        "utf8",
      )

      await expect(buildEmulatorResizeTransientAudit(dir)).resolves.toMatchObject({
        profileKind: "settle_probe",
        immediateFramesDegraded: ["immediate-small"],
        delayedFramesDegraded: [],
        churnFramesDegraded: [],
        classification: "immediate_transient_only",
        settledHistoryHasFinalTail: true,
        nonInitialViewportResetCount: 0,
      })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("classifies churn snapshots separately from settle-probe snapshots", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-resize-churn-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      for (const label of ["streaming-churn-0", "streaming-churn-1"]) {
        await fs.writeFile(path.join(dir, "observer_text", `${label}.txt`), "❯\n- item-01\n", "utf8")
      }
      await fs.writeFile(path.join(dir, "observer_text", "settled-history.txt"), "- item-20\n", "utf8")
      await fs.writeFile(
        path.join(dir, "viewport_resets.ndjson"),
        JSON.stringify({ event: "managed_viewport_reset_key" }) + "\n",
        "utf8",
      )
      await fs.writeFile(
        path.join(dir, "surface_model.ndjson"),
        JSON.stringify({ pendingResponse: true, landingVariant: "board", activeWindowHiddenCount: 0, activeWindowTruncated: false }) + "\n",
        "utf8",
      )

      await expect(buildEmulatorResizeTransientAudit(dir)).resolves.toMatchObject({
        profileKind: "streaming_churn",
        immediateFramesDegraded: [],
        delayedFramesDegraded: [],
        churnFramesDegraded: [],
        classification: "streaming_churn_green",
        settledHistoryHasFinalTail: true,
      })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
