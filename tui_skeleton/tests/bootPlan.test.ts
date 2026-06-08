import { describe, expect, it } from "vitest"
import {
  computeCliArgv,
  computeCliBootPlan,
  isLocalBaseUrl,
  parseEngineModeArgv,
  shouldSkipEngineForArgv,
  shouldUseIsolatedEngine,
} from "../src/cli_bootPlan.js"

describe("cli boot plan", () => {
  it("defaults bare invocation to repl", () => {
    const result = computeCliArgv(["node", "breadboard"])
    expect(result.defaultedToRepl).toBe(true)
    expect(result.argv).toEqual(["node", "breadboard", "repl"])
  })

  it("skips engine for opentui repl mode", () => {
    expect(shouldSkipEngineForArgv(["node", "breadboard", "repl", "--tui", "opentui"])).toBe(true)
  })

  it("does not skip engine for normal repl mode", () => {
    expect(shouldSkipEngineForArgv(["node", "breadboard", "repl"])).toBe(false)
  })

  it("recognizes local base urls", () => {
    expect(isLocalBaseUrl("http://127.0.0.1:9099")).toBe(true)
    expect(isLocalBaseUrl("http://localhost:9099")).toBe(true)
    expect(isLocalBaseUrl("https://api.example.com")).toBe(false)
  })

  it("enables isolated engine only for local run by default", () => {
    expect(shouldUseIsolatedEngine(["node", "breadboard", "run"], "http://127.0.0.1:9099", {})).toBe(true)
    expect(shouldUseIsolatedEngine(["node", "breadboard", "ask"], "http://127.0.0.1:9099", {})).toBe(false)
    expect(shouldUseIsolatedEngine(["node", "breadboard", "run"], "https://api.example.com", {})).toBe(false)
    expect(shouldUseIsolatedEngine(["node", "breadboard", "run"], "http://127.0.0.1:9099", { BREADBOARD_ENGINE_ISOLATED: "0" })).toBe(false)
  })

  it("computes the boot plan from raw argv", () => {
    const plan = computeCliBootPlan(["node", "breadboard"])
    expect(plan.defaultedToRepl).toBe(true)
    expect(plan.command).toBe("repl")
    expect(plan.shouldSkipEngine).toBe(false)
  })

  it("parses engine mode from repl argv for pre-command engine boot", () => {
    expect(parseEngineModeArgv(["node", "breadboard", "repl", "--engine-mode", "external"])).toBe("external")
    expect(parseEngineModeArgv(["node", "breadboard", "repl", "--engine-mode=local-owned"])).toBe("local-owned")
    expect(parseEngineModeArgv(["node", "breadboard", "repl"])).toBeUndefined()
  })
})
