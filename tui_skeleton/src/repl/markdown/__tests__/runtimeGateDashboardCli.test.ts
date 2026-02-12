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

describe("runtime_gate_dashboard CLI", () => {
  it("returns non-zero in strict mode when required artifacts are missing", () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "bb-runtime-dashboard-"))
    const result = spawnSync(
      "node",
      [
        "--import",
        "tsx",
        "scripts/runtime_gate_dashboard.ts",
        "--artifacts",
        tmp,
        "--strict",
      ],
      { cwd: path.resolve("."), encoding: "utf8" },
    )
    expect(result.status).toBe(1)
  })

  it("returns zero in strict mode when strict suite is confirmed and artifacts pass", () => {
    const tmp = mkdtempSync(path.join(os.tmpdir(), "bb-runtime-dashboard-"))
    writeJson(tmp, "jitter_gate.json", {
      ok: true,
      summary: "ok",
      comparison: { deltaPrefixChurn: 0, deltaReflowCount: 0 },
      thresholds: { maxPrefixChurnDelta: 0, maxReflowDelta: 0 },
    })
    writeJson(tmp, "noise_gate.json", {
      ok: true,
      summary: "ok",
      threshold: 0.8,
      metrics: { ratio: 0.2, canonicalChars: 100, noiseChars: 20 },
    })
    writeJson(tmp, "noise_matrix.json", {
      ok: true,
      failCount: 0,
      total: 2,
      results: [],
    })
    const outJson = path.join(tmp, "dashboard.json")
    const outMd = path.join(tmp, "dashboard.md")
    const result = spawnSync(
      "node",
      [
        "--import",
        "tsx",
        "scripts/runtime_gate_dashboard.ts",
        "--artifacts",
        tmp,
        "--strict-tests-ok",
        "--strict",
        "--out",
        outJson,
        "--markdown-out",
        outMd,
      ],
      { cwd: path.resolve("."), encoding: "utf8" },
    )
    expect(result.status).toBe(0)
  })
})
