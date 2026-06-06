import { describe, expect, it } from "vitest"
import { evaluateMaintenanceWrapperCompactSessionHeader } from "../tools/assertions/maintenanceWrapperCompactSessionHeaderCheck.ts"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"

const writeCase = async (payload: { snapshots: string; surface: string }) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-compact-header-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), payload.snapshots, "utf8")
  await writeFile(path.join(dir, "surface_model.ndjson"), payload.surface, "utf8")
  return dir
}

describe("maintenanceWrapperCompactSessionHeaderCheck", () => {
  it("accepts compact inline session-header evidence across a resize roundtrip", async () => {
    const dir = await writeCase({
      snapshots:
        "# after-resize-small\n" +
        "BreadBoard · Claude Code\n" +
        "gpt-5nano  ·  /repo  ·  turn 1  ·  s abc12345\n\n" +
        "# after-answer-small\n" +
        "dev   ·   /repo  ·  turn 1  ·  s abc12345\n" +
        "Verification: verification receipt present\n\n" +
        "# after-resize-tight\n" +
        "dev   ·   /repo  ·  turn 1  ·  s abc12345\n" +
        "Verification: verification receipt present\n",
      surface: '{"landingRetired":true,"warmLandingVisible":false,"showSessionHeaderInline":true}\n',
    })
    await expect(evaluateMaintenanceWrapperCompactSessionHeader(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("accepts preserved retired landing orientation with footer turn identity", async () => {
    const dir = await writeCase({
      snapshots:
        "# after-resize-small\nVerification: verification receipt present\n• [ready] mdl dev · turn 1\n\n" +
        "# after-answer-small\nVerification: verification receipt present\n• [ready] mdl dev · turn 1\n\n" +
        "# after-resize-tight\nVerification: verification receipt present\n• [ready] mdl dev · turn 1\n",
      surface: '{"landingRetired":true,"warmLandingVisible":false,"appendLandingToFeed":true,"pendingResponse":false,"showSessionHeaderInline":false}\n',
    })
    await expect(evaluateMaintenanceWrapperCompactSessionHeader(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing compact header state or rich landing revival", async () => {
    const dir = await writeCase({
      snapshots:
        "# after-answer-small\nTips for getting started\nVerification: verification receipt present\n\n" +
        "# after-resize-tight\nVerification: verification receipt present\n",
      surface: '{"landingRetired":true,"warmLandingVisible":false,"showSessionHeaderInline":false}\n',
    })
    const anomalies = await evaluateMaintenanceWrapperCompactSessionHeader(dir)
    expect(anomalies.map((item) => item.id)).toContain("missing-retired-landing-orientation-state")
    expect(anomalies.map((item) => item.id)).toContain("rich-landing-revived-inline-after-answer-small")
    expect(anomalies.map((item) => item.id)).toContain("missing-compact-session-identity-after-answer-small")
    await rm(dir, { recursive: true, force: true })
  })
})
