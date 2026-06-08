import os from "node:os"
import path from "node:path"
import { promises as fs } from "node:fs"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperEmulatorResize } from "../tools/assertions/liveWrapperEmulatorResizeCheck"

const tempRoots: string[] = []

afterEach(async () => {
  await Promise.all(tempRoots.splice(0).map((dir) => fs.rm(dir, { recursive: true, force: true })))
})

describe("evaluateLiveWrapperEmulatorResize", () => {
  it("accepts resize observer evidence and final answer", async () => {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), "bb-emulator-resize-"))
    tempRoots.push(root)
    await fs.mkdir(path.join(root, "observer_runtime"), { recursive: true })
    await fs.mkdir(path.join(root, "observer_text"), { recursive: true })
    await fs.writeFile(
      path.join(root, "observer_runtime", "wezterm-events.ndjson"),
      [
        "event=window-resized	cols=90	rows=24",
        "event=window-resized	cols=120	rows=36",
      ].join("\n") + "\n",
      "utf8",
    )
    await fs.writeFile(path.join(root, "observer_text", "after-answer.txt"), "RESIZE_OK\n", "utf8")

    await expect(evaluateLiveWrapperEmulatorResize(root)).resolves.toEqual([])
  })
})
