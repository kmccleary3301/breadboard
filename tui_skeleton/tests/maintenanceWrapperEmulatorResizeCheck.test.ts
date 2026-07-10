import { afterAll, describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile, mkdir } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateMaintenanceWrapperEmulatorResize } from "../tools/assertions/maintenanceWrapperEmulatorResizeCheck"

describe("maintenanceWrapperEmulatorResizeCheck", () => {
  const tempRoots: string[] = []

  afterAll(async () => {
    await Promise.all(tempRoots.map((dir) => rm(dir, { recursive: true, force: true })))
  })

  it("accepts the mock emulator resize contract", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "maintenance-wrapper-emulator-resize-"))
    tempRoots.push(root)
    await mkdir(path.join(root, "observer_runtime"), { recursive: true })
    await mkdir(path.join(root, "observer_text"), { recursive: true })
    await writeFile(path.join(root, "observer_runtime", "wezterm-events.ndjson"), [
      "event=window-resized\tcols=90\trows=24",
      "event=window-resized\tcols=120\trows=36",
    ].join("\n"))
    await writeFile(path.join(root, "observer_text", "after-answer.txt"), "Verification: verification receipt present\n")

    const anomalies = await evaluateMaintenanceWrapperEmulatorResize(root)
    expect(anomalies).toEqual([])
  })

  it("reports missing resize and settled output", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "maintenance-wrapper-emulator-resize-bad-"))
    tempRoots.push(root)
    await mkdir(path.join(root, "observer_runtime"), { recursive: true })
    await mkdir(path.join(root, "observer_text"), { recursive: true })
    await writeFile(path.join(root, "observer_runtime", "wezterm-events.ndjson"), "event=window-resized\tcols=90\trows=24\n")
    await writeFile(path.join(root, "observer_text", "after-answer.txt"), "")

    const anomalies = await evaluateMaintenanceWrapperEmulatorResize(root)
    expect(anomalies.map((item) => item.id)).toEqual([
      "emulator-missing-large-resize",
      "emulator-resize-answer-missing",
    ])
  })
})
