import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import path from "node:path"
import os from "node:os"
import { evaluateLandingLifecycle } from "../tools/assertions/landingLifecycleCheck.js"

const withCaseDir = async (files: Record<string, string>, fn: (dir: string) => Promise<void>) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "landing-lifecycle-check-"))
  try {
    for (const [name, body] of Object.entries(files)) {
      await writeFile(path.join(dir, name), body, "utf8")
    }
    await fn(dir)
  } finally {
    await rm(dir, { recursive: true, force: true })
  }
}

const line = (record: Record<string, unknown>): string => `${JSON.stringify({ event: "surface_model", ...record })}\n`

describe("evaluateLandingLifecycle", () => {
  it("accepts explicit startup visible and later retired lifecycle records", async () => {
    await withCaseDir(
      {
        "app_start_anchor.txt": JSON.stringify({ landingLifecycleState: "fresh-visible", landingLifecycleReason: "rich-landing-fits-active-region" }),
        "surface_model.ndjson":
          line({
            landingLifecycleState: "fresh-visible",
            landingLifecycleReason: "rich-landing-fits-active-region",
            landingLifecycleVisibleInline: true,
            landingLifecycleCommittedSnapshot: false,
            pendingResponse: false,
            transcriptCommittedCount: 0,
            transcriptTailCount: 0,
          }) +
          line({
            landingLifecycleState: "retired",
            landingLifecycleReason: "meaningful-interaction-displaced-warm-landing",
            landingLifecycleVisibleInline: false,
            landingLifecycleCommittedSnapshot: false,
            pendingResponse: false,
            transcriptCommittedCount: 2,
            transcriptTailCount: 0,
          }),
      },
      async (dir) => {
        expect(await evaluateLandingLifecycle(dir)).toEqual([])
      },
    )
  })

  it("rejects missing lifecycle state", async () => {
    await withCaseDir(
      {
        "surface_model.ndjson": line({
          landingLifecycleReason: "rich-landing-fits-active-region",
          pendingResponse: false,
          transcriptCommittedCount: 0,
          transcriptTailCount: 0,
        }),
      },
      async (dir) => {
        expect((await evaluateLandingLifecycle(dir)).map((item) => item.id)).toContain("landing-lifecycle-state-missing")
      },
    )
  })

  it("rejects invisible startup lifecycle before meaningful interaction", async () => {
    await withCaseDir(
      {
        "surface_model.ndjson": line({
          landingLifecycleState: "retired",
          landingLifecycleReason: "landing-retired-after-meaningful-interaction",
          landingLifecycleVisibleInline: false,
          landingLifecycleCommittedSnapshot: false,
          pendingResponse: false,
          transcriptCommittedCount: 0,
          transcriptTailCount: 0,
        }),
      },
      async (dir) => {
        expect((await evaluateLandingLifecycle(dir)).map((item) => item.id)).toContain("landing-lifecycle-startup-not-visible")
      },
    )
  })
})
