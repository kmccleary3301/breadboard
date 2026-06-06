import path from "node:path"
import { describe, expect, it } from "vitest"
import { resolveCaseCommandCwd } from "../scripts/harness/caseCommandCwd.ts"

const rootDir = process.cwd()
const repoRoot = path.resolve(rootDir, "..")
const dummyWorkspaceCwd = "/tmp/bb_qc_terminal_dummy_workspace"

const resolve = (cwd: string | undefined, command = "bb repl", allowProjectCwd = false) =>
  resolveCaseCommandCwd({
    testCase: { cwd },
    command,
    rootDir,
    repoRoot,
    dummyWorkspaceCwd,
    allowProjectCwd,
  })

describe("resolveCaseCommandCwd", () => {
  it("preserves absolute dummy workspace paths", () => {
    expect(resolve("/tmp/bb_abs_dummy")).toBe("/tmp/bb_abs_dummy")
  })

  it("resolves relative case paths inside the TUI root", () => {
    expect(resolve("tmp/relative_dummy")).toBe(path.join(rootDir, "tmp/relative_dummy"))
  })

  it("reroutes installed bb repl from repo root to the QC dummy workspace unless explicitly allowed", () => {
    expect(resolve("..")).toBe(dummyWorkspaceCwd)
  })

  it("allows installed bb repl from repo root when the project-cwd escape hatch is set", () => {
    expect(resolve("..", "bb repl", true)).toBe(repoRoot)
  })
})
