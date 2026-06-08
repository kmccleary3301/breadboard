import { afterEach, describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateLiveWrapperStreaming } from "../tools/assertions/liveWrapperStreamingCheck"

describe("liveWrapperStreamingCheck", () => {
  let tempDir: string | null = null

  afterEach(async () => {
    if (tempDir) {
      await rm(tempDir, { recursive: true, force: true })
      tempDir = null
    }
  })

  it("passes when replay-backed events contain assistant streaming deltas", async () => {
    tempDir = await mkdtemp(path.join(os.tmpdir(), "bbw-live-streaming-"))
    await writeFile(path.join(tempDir, "events.ndjson"), `${JSON.stringify({ type: "assistant.message.delta", payload: { delta: "PING_OK" } })}\n`, "utf8")
    await writeFile(path.join(tempDir, "repl_state.ndjson"), `${JSON.stringify({ state: { lastConversation: { speaker: "user", phase: "final" } } })}\n`, "utf8")

    await expect(evaluateLiveWrapperStreaming(tempDir)).resolves.toEqual([])
  })

  it("flags missing replay-backed assistant stream events", async () => {
    tempDir = await mkdtemp(path.join(os.tmpdir(), "bbw-live-streaming-"))
    await writeFile(path.join(tempDir, "events.ndjson"), `${JSON.stringify({ type: "assistant.message", payload: { content: "final only" } })}\n`, "utf8")
    await writeFile(path.join(tempDir, "repl_state.ndjson"), `${JSON.stringify({ state: { lastConversation: { speaker: "assistant", phase: "final" } } })}\n`, "utf8")

    await expect(evaluateLiveWrapperStreaming(tempDir)).resolves.toEqual([
      {
        id: "assistant-stream-event-missing",
        message: "Expected replay-backed events.ndjson to contain an assistant streaming delta event.",
      },
    ])
  })
})
