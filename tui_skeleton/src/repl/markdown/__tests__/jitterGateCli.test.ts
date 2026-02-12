import { mkdtempSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { describe, expect, it } from "vitest"

const writeJson = (dir: string, name: string, value: unknown): string => {
  const target = path.join(dir, name)
  writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`, "utf8")
  return target
}

describe("runtime_jitter_gate CLI", () => {
  it("returns non-zero in strict mode when candidate exceeds threshold", () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "bb-jitter-cli-"))
    const baseline = writeJson(tmp, "baseline.json", ["x\ny\nz", "x\ny\nz\nw"])
    const candidate = writeJson(tmp, "candidate.json", ["x\ny\nz", "x\nY\nz"])
    const result = spawnSync(
      "node",
      [
        "--import",
        "tsx",
        "scripts/runtime_jitter_gate.ts",
        "--baseline",
        baseline,
        "--candidate",
        candidate,
        "--max-prefix-delta",
        "0",
        "--max-reflow-delta",
        "0",
        "--strict",
      ],
      { cwd: path.resolve("."), encoding: "utf8" },
    )
    expect(result.status).toBe(1)
  })

  it("returns zero in strict mode when candidate passes threshold", () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "bb-jitter-cli-"))
    const baseline = writeJson(tmp, "baseline.json", ["x\ny\nz", "x\nY\nz"])
    const candidate = writeJson(tmp, "candidate.json", ["x\ny\nz", "x\ny\nz\nw"])
    const result = spawnSync(
      "node",
      [
        "--import",
        "tsx",
        "scripts/runtime_jitter_gate.ts",
        "--baseline",
        baseline,
        "--candidate",
        candidate,
        "--max-prefix-delta",
        "0",
        "--max-reflow-delta",
        "0",
        "--strict",
      ],
      { cwd: path.resolve("."), encoding: "utf8" },
    )
    expect(result.status).toBe(0)
  })
})
