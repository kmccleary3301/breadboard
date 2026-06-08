import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"

import { afterEach, describe, expect, it } from "vitest"

import { startReplayServer } from "../tools/mock/replaySse.ts"

const tempPaths: string[] = []

afterEach(async () => {
  await Promise.all(tempPaths.splice(0).map((entry) => fs.rm(entry, { recursive: true, force: true })))
})

const writeReplayScript = async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-replay-sse-shutdown-"))
  tempPaths.push(dir)
  const scriptPath = path.join(dir, "script.json")
  await fs.writeFile(
    scriptPath,
    JSON.stringify({ steps: [{ delayMs: 10_000, event: { type: "assistant_message", data: { text: "late" } } }] }),
    "utf8",
  )
  return scriptPath
}

const closeWithin = async (promise: Promise<void>, ms: number) => {
  return await Promise.race([
    promise.then(() => "closed" as const),
    new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), ms)),
  ])
}

describe("replay SSE shutdown", () => {
  it("closes while an SSE client is still connected", async () => {
    const handle = await startReplayServer({ scriptPath: await writeReplayScript(), port: 0 })
    const sessionResponse = await fetch(`${handle.url}/sessions`, { method: "POST" })
    const session = (await sessionResponse.json()) as { session_id: string }
    const eventsResponse = await fetch(`${handle.url}/sessions/${session.session_id}/events`)

    expect(eventsResponse.ok).toBe(true)
    await expect(closeWithin(handle.close(), 3_000)).resolves.toBe("closed")
  })
})
