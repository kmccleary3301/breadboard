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
    name: "rejects a product route missing from the ordinary client",
    before:
      '    case "public.artifact.verify": return request("POST", `/v1/artifacts/${identifier(input, "artifact_id")}/verify`)',
    after: "",
    diagnostic: "OpenAPI routes missing from product client: POST /v1/artifacts/{}/verify",
  },
  {
    name: "rejects an ordinary client route missing from the product contract",
    before: '`/v1/artifacts/${identifier(input, "artifact_id")}/verify`',
    after: '`/v1/artifacts/${identifier(input, "artifact_id")}/unowned`',
    diagnostic: "Client routes missing from OpenAPI contract: POST /v1/artifacts/{}/unowned",
  },
  {
    name: "rejects removal of the product session event stream",
    before: "      return streamSessionEvents(sessionId, { config, query, lastEventId })",
    after: "      return []",
    diagnostic: "OpenAPI routes missing from product client: GET /v1/sessions/{}/events",
  },
] as const

describe("generated API client verifier", () => {
  it.each(negativeCases)("$name", ({ before, after, diagnostic }) => {
    const verifierCopy = prepareVerifierCopy()
    try {
      const source = readFileSync(verifierCopy.clientPath, "utf8").replace(/\r\n?/g, "\n")
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
