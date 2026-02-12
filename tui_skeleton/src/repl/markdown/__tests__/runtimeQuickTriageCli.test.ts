import { existsSync, mkdtempSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { spawnSync } from "node:child_process"
import { describe, expect, it } from "vitest"

describe("runtime_quick_triage_snapshots CLI", () => {
  it("generates triage pack artifacts for fixture scenarios", () => {
    const outDir = mkdtempSync(path.join(os.tmpdir(), "bb-runtime-triage-"))
    const fixtures = [
      "src/commands/repl/__tests__/fixtures/tool_call_result.jsonl",
      "src/commands/repl/__tests__/fixtures/multi_turn_interleave.jsonl",
    ].join(",")
    const result = spawnSync(
      "node",
      [
        "--import",
        "tsx",
        "scripts/runtime_quick_triage_snapshots.ts",
        "--out-dir",
        outDir,
        "--fixtures",
        fixtures,
      ],
      { cwd: path.resolve("."), encoding: "utf8" },
    )
    expect(result.status).toBe(0)
    expect(existsSync(path.join(outDir, "index.json"))).toBe(true)
    expect(existsSync(path.join(outDir, "_diffs", "index.json"))).toBe(true)
  })
})
