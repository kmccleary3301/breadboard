import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"
import { evaluateCommandArgumentSurface } from "../tools/assertions/commandArgumentSurfaceCheck.js"

const writeCase = async (body: string): Promise<string> => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-command-args-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), body, "utf8")
  return dir
}

describe("evaluateCommandArgumentSurface", () => {
  it("accepts malformed command argument rejection evidence", async () => {
    const dir = await writeCase(
      [
        "# invalid-mode-result",
        "Invalid mode: chaos",
        "Expected: plan|build|auto",
        "# missing-model-result",
        "Missing required argument <id>",
        "Usage: /model <id>",
        "# extra-help-result",
        "/help does not take arguments",
        "Usage: /help",
      ].join("\n"),
    )
    await expect(evaluateCommandArgumentSurface(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("rejects missing validation and model fallthrough", async () => {
    const dir = await writeCase(
      [
        "# invalid-mode-result",
        "Mock assistant: hello",
        "# missing-model-result",
        "Usage: /model <id>",
        "# extra-help-result",
        "Usage: /help",
      ].join("\n"),
    )
    const anomalies = await evaluateCommandArgumentSurface(dir)
    expect(anomalies.map((item) => item.id)).toEqual(
      expect.arrayContaining(["invalid-mode-not-rejected", "missing-model-id-not-rejected", "extra-help-arg-not-rejected", "malformed-command-submitted-to-model"]),
    )
    await rm(dir, { recursive: true, force: true })
  })
})
