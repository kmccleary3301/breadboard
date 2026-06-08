import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateRecentSessionsOverlay } from "../tools/assertions/recentSessionsOverlayCheck.ts"

const writeCase = async (snapshots: string, stateDump: string) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-recent-sessions-overlay-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  await writeFile(path.join(dir, "state_dump.ndjson"), stateDump, "utf8")
  return dir
}

describe("evaluateRecentSessionsOverlay", () => {
  it("accepts recent session overlay evidence", async () => {
    const dir = await writeCase(
      [
        "# recent-sessions-open",
        "Recent sessions",
        "↑/↓ select • PgUp/PgDn page • Enter attach • R refresh • Esc close",
        "› session-123",
        "# recent-sessions-close",
        'Try "refactor <filepath>"',
        "",
      ].join("\n"),
      ['{"state":{"sessionId":"session-123"}}'].join("\n"),
    )
    await expect(evaluateRecentSessionsOverlay(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("accepts composer/footer as base-shell close evidence", async () => {
    const dir = await writeCase(
      [
        "# recent-sessions-open",
        "Recent sessions",
        "↑/↓ select • PgUp/PgDn page • Enter attach • R refresh • Esc close",
        "› session-123",
        "# recent-sessions-close",
        "Mock assistant: hello world",
        "❯   Type your request…",
        "• [ready] last 1s · enter send",
        "resume /sessions · @ attach",
        "",
      ].join("\n"),
      ['{"state":{"sessionId":"session-123"}}'].join("\n"),
    )
    await expect(evaluateRecentSessionsOverlay(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })

  it("flags missing overlay details", async () => {
    const dir = await writeCase(
      [
        "# recent-sessions-open",
        "overlay",
        "# recent-sessions-close",
        "overlay",
        "",
      ].join("\n"),
      ['{"state":{"sessionId":"session-xyz"}}'].join("\n"),
    )
    const anomalies = await evaluateRecentSessionsOverlay(dir)
    expect(anomalies.map((item) => item.id)).toContain("overlay-title-missing")
    expect(anomalies.map((item) => item.id)).toContain("overlay-hint-missing")
    expect(anomalies.map((item) => item.id)).toContain("session-id-missing")
    expect(anomalies.map((item) => item.id)).toContain("overlay-close-base-missing")
    await rm(dir, { recursive: true, force: true })
  })
})
