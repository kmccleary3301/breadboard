import { spawnSync } from "node:child_process"
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

const TUI_ROOT = path.resolve(".")
const REPO_ROOT = path.resolve(TUI_ROOT, "..")

type VerifierCopy = {
  readonly root: string
  readonly clientPath: string
  readonly scriptPath: string
}

const prepareVerifierCopy = (): VerifierCopy => {
  const root = mkdtempSync(path.join(os.tmpdir(), "bb-generated-client-verifier-"))
  const tuiRoot = path.join(root, "tui_skeleton")
  const scriptPath = path.join(tuiRoot, "scripts", "generate_api_client.ts")
  const clientPath = path.join(tuiRoot, "src", "api", "client.ts")
  const copies = [
    [path.join(TUI_ROOT, "package.json"), path.join(tuiRoot, "package.json")],
    [path.join(TUI_ROOT, "scripts", "generate_api_client.ts"), scriptPath],
    [path.join(TUI_ROOT, "src", "api", "client.ts"), clientPath],
    [path.join(TUI_ROOT, "src", "api", "types.ts"), path.join(tuiRoot, "src", "api", "types.ts")],
    [
      path.join(TUI_ROOT, "src", "api", "generated", "client-route-manifest.json"),
      path.join(tuiRoot, "src", "api", "generated", "client-route-manifest.json"),
    ],
    [
      path.join(TUI_ROOT, "src", "api", "generated", "openapi-types.ts"),
      path.join(tuiRoot, "src", "api", "generated", "openapi-types.ts"),
    ],
    [
      path.join(REPO_ROOT, "docs", "contracts", "cli_bridge", "openapi.json"),
      path.join(root, "docs", "contracts", "cli_bridge", "openapi.json"),
    ],
  ] as const

  for (const [source, destination] of copies) {
    mkdirSync(path.dirname(destination), { recursive: true })
    copyFileSync(source, destination)
  }
  return { root, clientPath, scriptPath }
}

const negativeCases = [
  {
    name: "rejects a missing request DTO even when the response DTO remains generated",
    before:
      "postInput: (sessionId: string, body: SessionInputRequest) =>\n    requestWithConfig<SessionInputResponse>",
    after:
      "postInput: (sessionId: string, body: Record<string, unknown>) =>\n    requestWithConfig<SessionInputResponse>",
    diagnostic: "POST /v1/sessions/{}/input request expected one of SessionInputRequest",
  },
  {
    name: "rejects a missing response DTO even when the request DTO remains generated",
    before: "requestWithConfig<SessionInputResponse>(config, `/v1/sessions/${sessionId}/input`",
    after: "requestWithConfig<unknown>(config, `/v1/sessions/${sessionId}/input`",
    diagnostic:
      "POST /v1/sessions/{}/input response -> unknown (expected one of SessionInputResponse)",
  },
  {
    name: "rejects removal of the direct attachment-upload route",
    before: "const url = buildUrl(config.baseUrl, `/v1/sessions/${sessionId}/attachments`)",
    after: "const url = config.baseUrl",
    diagnostic: "Required direct-fetch client routes are missing: POST /v1/sessions/{}/attachments",
  },
] as const

describe("generated API client verifier", () => {
  it.each(negativeCases)("$name", ({ before, after, diagnostic }) => {
    const verifierCopy = prepareVerifierCopy()
    try {
      const source = readFileSync(verifierCopy.clientPath, "utf8")
      if (!source.includes(before)) {
        throw new Error(`Generated-client negative fixture anchor is missing: ${before}`)
      }
      writeFileSync(verifierCopy.clientPath, source.replace(before, after), "utf8")

      const result = spawnSync(
        process.execPath,
        ["--import", "tsx", verifierCopy.scriptPath, "--check"],
        { cwd: TUI_ROOT, encoding: "utf8" },
      )

      expect(result.status).toBe(1)
      expect(result.stderr).toContain(diagnostic)
    } finally {
      rmSync(verifierCopy.root, { recursive: true, force: true })
    }
  })
})
