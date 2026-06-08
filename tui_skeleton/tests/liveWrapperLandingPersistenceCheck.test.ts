import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"
import { evaluateLandingPersistence } from "../tools/assertions/liveWrapperLandingPersistenceCheck.ts"

const tempDirs: string[] = []

const makeCaseDir = async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-landing-check-"))
  tempDirs.push(dir)
  return dir
}

afterEach(async () => {
  await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })))
})

describe("liveWrapperLandingPersistenceCheck", () => {
  it("accepts a case where the landing stays warm on first submit", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# after-submit",
        "Tips for getting started",
        "❯ Answer with exactly: PING_OK",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: false, appendLandingToFeed: false, warmLandingVisible: true }),
      ].join("\n"),
      "utf8",
    )

    await expect(evaluateLandingPersistence(dir)).resolves.toEqual([])
  })

  it("accepts a case where the landing has already been preserved as a committed snapshot", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# after-submit",
        "BreadBoard v0.2.0",
        "❯ What is 1+1?",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({
          pendingResponse: true,
          transcriptCommittedCount: 1,
          transcriptTailCount: 0,
          landingRetired: true,
          appendLandingToFeed: true,
          warmLandingVisible: false,
          landingLifecycleCommittedSnapshot: true,
        }),
      ].join("\n"),
      "utf8",
    )

    await expect(evaluateLandingPersistence(dir)).resolves.toEqual([])
  })

  it("flags a case where landing retires before the first assistant tail", async () => {
    const dir = await makeCaseDir()
    await writeFile(
      path.join(dir, "pty_snapshots.txt"),
      [
        "# after-submit",
        "❯ Answer with exactly: PING_OK",
        "",
      ].join("\n"),
      "utf8",
    )
    await writeFile(
      path.join(dir, "surface_model.ndjson"),
      [
        JSON.stringify({ pendingResponse: true, transcriptCommittedCount: 1, transcriptTailCount: 0, landingRetired: true, appendLandingToFeed: true, warmLandingVisible: false }),
      ].join("\n"),
      "utf8",
    )

    await expect(evaluateLandingPersistence(dir)).resolves.toEqual([
      { id: "landing-missing", message: 'Snapshot "after-submit" does not retain the rich landing content.' },
      {
        id: "landing-retired-too-early",
        message: "Landing retired, was appended to history, or stopped being warm before any assistant tail appeared.",
      },
    ])
  })
})
