import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/misc/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    dispatchSlashCommand: (command: string, args: string[]) => Promise<void>
    getState: () => any
    pendingResponse: boolean
    mainFollowTail: boolean
  }

describe("follow slash command", () => {
  it("pauses and resumes follow for the current run", async () => {
    const controller = createController()
    controller.pendingResponse = true

    await controller.dispatchSlashCommand("follow", ["pause"])
    expect(controller.getState().mainFollowTail).toBe(false)

    await controller.dispatchSlashCommand("follow", ["resume"])
    expect(controller.getState().mainFollowTail).toBe(true)
  })

  it("reports default live behavior when idle", async () => {
    const controller = createController()

    await controller.dispatchSlashCommand("follow", [])

    const hints = controller.getState().hints as string[]
    expect(hints[hints.length - 1]).toContain("Default behavior is live follow")
  })
})
