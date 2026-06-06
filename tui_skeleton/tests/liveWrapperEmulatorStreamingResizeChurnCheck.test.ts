import { describe, expect, it } from "vitest"
import { mkdtempSync, rmSync } from "node:fs"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import { evaluateLiveWrapperEmulatorStreamingResizeChurn } from "../tools/assertions/liveWrapperEmulatorStreamingResizeChurnCheck.ts"

describe("liveWrapperEmulatorStreamingResizeChurnCheck", () => {
  it("accepts stable streaming resize churn frames", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-streaming-churn-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      await fs.writeFile(
        path.join(dir, "observer_text", "streaming-churn-0.txt"),
        "❯\n- item-01\n",
        "utf8",
      )
      for (const label of [
        "streaming-churn-1",
        "streaming-churn-2",
        "streaming-churn-3",
        "streaming-churn-4",
        "streaming-churn-5",
      ]) {
        await fs.writeFile(path.join(dir, "observer_text", `${label}.txt`), "- item-02\n❯\n", "utf8")
      }
      await fs.writeFile(path.join(dir, "observer_text", "streaming-churn-5.txt"), "- item-20\n❯\n", "utf8")
      await fs.writeFile(path.join(dir, "observer_text", "settled-history.txt"), "- item-19\n", "utf8")
      await fs.writeFile(path.join(dir, "viewport_resets.ndjson"), JSON.stringify({ event: "managed_viewport_reset_key" }) + "\n", "utf8")
      await expect(evaluateLiveWrapperEmulatorStreamingResizeChurn(dir)).resolves.toEqual([])
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it("flags repeated pending reset-key churn", async () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), "bb-emulator-streaming-churn-"))
    try {
      await fs.mkdir(path.join(dir, "observer_text"), { recursive: true })
      for (const label of [
        "streaming-churn-0",
        "streaming-churn-1",
        "streaming-churn-2",
        "streaming-churn-3",
        "streaming-churn-4",
        "streaming-churn-5",
      ]) {
        await fs.writeFile(path.join(dir, "observer_text", `${label}.txt`), "- item-20\n❯\n", "utf8")
      }
      await fs.writeFile(path.join(dir, "observer_text", "settled-history.txt"), "- item-20\n", "utf8")
      await fs.writeFile(
        path.join(dir, "viewport_resets.ndjson"),
        [
          { event: "managed_viewport_reset_key", pendingResponse: false },
          { event: "managed_viewport_reset_key", pendingResponse: true },
          { event: "managed_viewport_reset_key", pendingResponse: true },
        ].map((entry) => JSON.stringify(entry)).join("\n") + "\n",
        "utf8",
      )
      const anomalies = await evaluateLiveWrapperEmulatorStreamingResizeChurn(dir)
      expect(anomalies.map((entry) => entry.id)).toContain("viewport-reset-churn")
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
