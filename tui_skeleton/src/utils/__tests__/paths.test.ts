import { describe, expect, it } from "vitest"
import path from "node:path"
import { resolveBreadboardWorkspace, resolveBreadboardWorkspaceOrCwd } from "../paths.js"

describe("resolveBreadboardWorkspace", () => {
  it("returns undefined when workspace is not provided", () => {
    expect(resolveBreadboardWorkspace(undefined)).toBeUndefined()
    expect(resolveBreadboardWorkspace(null)).toBeUndefined()
    expect(resolveBreadboardWorkspace("   ")).toBeUndefined()
  })

  it("resolves explicit workspace paths", () => {
    const resolved = resolveBreadboardWorkspace("tui_skeleton")
    expect(typeof resolved).toBe("string")
    expect(path.isAbsolute(resolved!)).toBe(true)
    expect(resolved).toContain("tui_skeleton")
  })
})

describe("resolveBreadboardWorkspaceOrCwd", () => {
  it("defaults to cwd when workspace is omitted", () => {
    const cwd = process.cwd()
    const resolved = resolveBreadboardWorkspaceOrCwd(undefined, cwd)
    expect(resolved).toBe(path.resolve(cwd))
  })

  it("preserves explicit workspace overrides", () => {
    const resolved = resolveBreadboardWorkspaceOrCwd("tui_skeleton", "/tmp/ignored")
    expect(typeof resolved).toBe("string")
    expect(path.isAbsolute(resolved)).toBe(true)
    expect(resolved).toContain("tui_skeleton")
  })
})
