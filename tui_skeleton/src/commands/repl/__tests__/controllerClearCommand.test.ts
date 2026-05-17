import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/misc/test_simple_native.yaml",
    workspace: ".",
  }) as any

describe("clear command", () => {
  it("clears stale user-facing hints before showing the clear confirmation", async () => {
    const controller = createController()
    controller.hints.push("Mode set request sent (auto).")
    controller.hints.push("Loaded 1 checkpoint.")

    await controller.slashHandlers().clear([])

    expect(controller.viewClearAt).toEqual(expect.any(Number))
    expect(controller.hints).toEqual(["Cleared view (history preserved)."])
  })
})
