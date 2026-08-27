import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "@breadboard/sdk"
import type { SessionEvent } from "@breadboard/sdk"
import { CliProviders } from "../../../providers/cliProviders.js"
import { ReplSessionController } from "../controller.js"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("todo stale semantics", () => {
  it("marks existing scopes stale when the public session lifecycle resets a lost resume window", async () => {
    vi.spyOn(CliProviders.sdk.api(), "createSession").mockResolvedValue({
      created_at: "2026-07-13T00:00:00Z",
      session_id: "session-reconnect",
      status: "running",
    })
    vi.spyOn(CliProviders.sdk.api(), "getSessionSummary").mockResolvedValue({
      created_at: "2026-07-13T00:00:00Z",
      last_activity_at: "2026-07-13T00:00:00Z",
      session_id: "session-reconnect",
      status: "running",
    })
    vi.spyOn(CliProviders.sdk.api(), "getCtreeSnapshot").mockResolvedValue({})
    vi.spyOn(CliProviders.sdk, "stream").mockImplementation(
      async function* reconnectingStream(): AsyncGenerator<SessionEvent> {
        throw new ApiError("resume window expired", 409)
      },
    )
    const controller = new ReplSessionController({ configPath: "agent_configs/misc/test_simple_native.yaml" })

    try {
      await controller.start()
      await vi.waitFor(() => {
        expect(controller.getState().todoScopeStale).toBe(true)
      })
    } finally {
      await controller.stop()
    }
  })
})

