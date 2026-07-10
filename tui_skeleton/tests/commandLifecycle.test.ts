import { describe, expect, it } from "vitest"
import {
  renderCreatedPathLine,
  renderEngineLogsLine,
  renderEngineServeLine,
  renderEngineShutdownLine,
  renderEngineStartedLine,
  renderNoManagedEngineStoppedLine,
  renderPressCtrlCToStopLine,
  renderStoppedEngineLine,
  renderStoppedSessionLine,
} from "../src/commands/commandLifecycle.js"

describe("commandLifecycle", () => {
  it("renders created path lines", () => {
    expect(renderCreatedPathLine("/tmp/plugin.json")).toBe("Created /tmp/plugin.json")
  })

  it("renders stopped session lines", () => {
    expect(renderStoppedSessionLine("s-123")).toBe("Stopped session s-123")
  })

  it("renders engine startup and log lines", () => {
    expect(renderEngineStartedLine("http://127.0.0.1:9099", 42)).toBe("Engine started: http://127.0.0.1:9099 (pid 42)")
    expect(renderEngineLogsLine("/tmp/engine.log")).toBe("Logs: /tmp/engine.log")
  })

  it("renders engine serve and shutdown lines", () => {
    expect(renderEngineServeLine("http://127.0.0.1:9099", 42, true)).toBe("Engine: http://127.0.0.1:9099 (pid 42) [started]")
    expect(renderEngineServeLine("http://127.0.0.1:9099", null, false)).toBe("Engine: http://127.0.0.1:9099")
    expect(renderPressCtrlCToStopLine()).toBe("Press Ctrl-C to stop.")
    expect(renderEngineShutdownLine(true)).toBe("Engine stopped.")
    expect(renderEngineShutdownLine(false)).toBe("Engine stop requested.")
  })

  it("renders engine stop result lines", () => {
    expect(renderStoppedEngineLine(42)).toBe("Stopped engine (pid 42).")
    expect(renderStoppedEngineLine(null)).toBe("Stopped engine.")
    expect(renderNoManagedEngineStoppedLine(42)).toBe("No managed engine stopped (pid 42).")
    expect(renderNoManagedEngineStoppedLine(null)).toBe("No managed engine stopped.")
  })
})
