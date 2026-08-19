import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"
import readline from "node:readline"
import { describe, expect, it } from "vitest"
import { createApiClient } from "../client.js"

const repoRoot = fileURLToPath(new URL("../../../..", import.meta.url))
const smokeServerScript = path.join(repoRoot, "tests", "test_sdk_v1_default_server_smoke.py")

const startDefaultServer = async (): Promise<{
  baseUrl: string
  child: ChildProcessWithoutNullStreams
}> => {
  const repoPython = path.join(repoRoot, ".venv", "bin", "python")
  const python = process.env.BREADBOARD_PYTHON ?? (existsSync(repoPython) ? repoPython : "python3")
  const child = spawn(python, [smokeServerScript, "--serve"], {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONPATH: [repoRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    },
    stdio: ["pipe", "pipe", "pipe"],
  })
  let stderr = ""
  child.stderr.setEncoding("utf8")
  child.stderr.on("data", (chunk) => {
    stderr += chunk
  })
  const lines = readline.createInterface({ input: child.stdout })
  const baseUrl = await new Promise<string>((resolve, reject) => {
    const onExit = (code: number | null) => {
      reject(new Error(`default server exited before startup (${code}): ${stderr}`))
    }
    child.once("exit", onExit)
    lines.once("line", (line) => {
      child.off("exit", onExit)
      lines.close()
      resolve(line.trim())
    })
  })
  return { baseUrl, child }
}

const data = (result: unknown): Record<string, unknown> =>
  (result as { data: Record<string, unknown> }).data

describe("TUI product client default server", () => {
  it("runs the product Session lifecycle without legacy routes", { timeout: 20_000 }, async () => {
    const { baseUrl, child } = await startDefaultServer()
    try {
      expect((await fetch(`${baseUrl}/sessions`)).status).toBe(404)

      const client = createApiClient({ baseUrl, requestTimeoutMs: 5_000 })
      expect((await client.invokePublicAction("public.system.health") as { ok: boolean }).ok).toBe(true)
      const created = data(await client.invokePublicAction("public.harness.create"))
      const locked = data(await client.invokePublicAction("public.harness.lock", {
        harness_id: created.path,
      }))
      const started = data(await client.invokePublicAction("public.session.start", {
        lock_id: locked.path,
        task: "Exercise the TUI product client",
        session_id: "tui-v1-smoke-session",
        idempotency_key: "start-smoke",
      }))
      const session = started.session as { session_id: string }
      const sent = data(await client.invokePublicAction("public.session.send_input", {
        session_id: session.session_id,
        content: "Continue",
        idempotency_key: "input-smoke",
      }))
      const canceled = data(await client.invokePublicAction("public.session.cancel", {
        session_id: session.session_id,
        reason: "smoke complete",
        idempotency_key: "cancel-smoke",
      }))
      const events = await client.invokePublicAction("public.session.events", {
        session_id: session.session_id,
      }) as AsyncGenerator<{ type: string }>
      const eventTypes: string[] = []
      for await (const event of events) eventTypes.push(event.type)

      expect((sent.session as { event_count: number }).event_count).toBeGreaterThanOrEqual(2)
      expect((canceled.session as { status: string }).status).toBe("canceled")
      expect(eventTypes[eventTypes.length - 1]).toBe("session.canceled")
    } finally {
      child.stdin.end()
      await new Promise<void>((resolve, reject) => {
        child.once("exit", (code) => {
          if (code === 0) resolve()
          else reject(new Error(`default server exited ${code}`))
        })
      })
    }
  })
})
