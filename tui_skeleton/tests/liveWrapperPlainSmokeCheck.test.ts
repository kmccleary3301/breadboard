import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLiveWrapperPlainSmoke } from "../tools/assertions/liveWrapperPlainSmokeCheck.ts"

const tempDirs: string[] = []
const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-live-plain-check-"))
  tempDirs.push(dir)
  return dir
}
afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperPlainSmokeCheck", () => {
  it("passes when landing persists, assistant streams, and the settled snapshot shows output plus ready", async () => {
    const dir = await makeCaseDir()
    await writeFile(path.join(dir, "pty_snapshots.txt"), "# after-submit\nTips for getting started\n\n# after-answer\ntwo\nType your request\n", "utf8")
    await writeFile(path.join(dir, "surface_model.ndjson"), JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }) + "\n", "utf8")
    await writeFile(path.join(dir, "events.ndjson"), JSON.stringify({ type: "assistant.message.delta", payload: { delta: "PING" } }) + "\n", "utf8")
    await writeFile(path.join(dir, "repl_state.ndjson"), JSON.stringify({ state: { lastConversation: { speaker: "assistant", phase: "streaming" } } }) + "\n", "utf8")
    await expect(evaluateLiveWrapperPlainSmoke(dir)).resolves.toEqual([])
  })

  it("fails when the visible assistant response leaks system prompt text", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# after-submit",
        "❯ Hi! How are you?",
        "",
        "# after-answer",
        "❯ Hi! How are you?",
        "You are Codex, based on GPT-5. You are running as a coding agent in the Codex CLI on a user's computer.",
        "two",
        "[ready]",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(path.join(dir, "surface_model.ndjson"), JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }) + "\n", "utf8")
    await writeFile(path.join(dir, "events.ndjson"), JSON.stringify({ type: "assistant.message.delta", payload: { delta: "You are Codex" } }) + "\n", "utf8")
    await writeFile(path.join(dir, "repl_state.ndjson"), JSON.stringify({ state: { lastConversation: { speaker: "assistant", phase: "streaming" } } }) + "\n", "utf8")

    await expect(evaluateLiveWrapperPlainSmoke(dir)).resolves.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "system-prompt-visible" }),
      ]),
    )
  })

  it("fails when a clipped prompt leak only exposes later formatting sections", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# after-submit",
        "❯ Hi! How are you?",
        "",
        "# after-answer",
        "### Final answer structure and style guidelines",
        "- Plain text; CLI handles styling.",
        "two",
        "Type your request",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(path.join(dir, "surface_model.ndjson"), JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }) + "\n", "utf8")
    await writeFile(path.join(dir, "events.ndjson"), JSON.stringify({ type: "assistant.message.delta", payload: { delta: "### Final answer structure and style guidelines" } }) + "\n", "utf8")
    await writeFile(path.join(dir, "repl_state.ndjson"), JSON.stringify({ state: { lastConversation: { speaker: "assistant", phase: "streaming" } } }) + "\n", "utf8")

    await expect(evaluateLiveWrapperPlainSmoke(dir)).resolves.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "system-prompt-visible" }),
      ]),
    )
  })
})
