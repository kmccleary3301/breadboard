import assert from "node:assert/strict"
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process"
import { existsSync } from "node:fs"
import { fileURLToPath } from "node:url"
import path from "node:path"
import readline from "node:readline"
import test from "node:test"

import { createBreadboardClient } from "../dist/client.js"
import { streamSessionEvents } from "../dist/stream.js"

const repoRoot = fileURLToPath(new URL("../../..", import.meta.url))
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

test(
  "TypeScript SDK runs the README flow against default create_app routes",
  { timeout: 20_000 },
  async (t) => {
    const { baseUrl, child } = await startDefaultServer()
    t.after(async () => {
      child.stdin.end()
      await new Promise<void>((resolve, reject) => {
        child.once("exit", (code) => {
          if (code === 0) resolve()
          else reject(new Error(`default server exited ${code}`))
        })
      })
    })

    assert.equal((await fetch(`${baseUrl}/sessions`)).status, 404)

    const client = createBreadboardClient({ baseUrl, requestTimeoutMs: 5_000 })
    assert.equal((await client.health()).status, "ok")
    const session = await client.createSession({
      config_path: "agent_configs/templates/minimal_harness.v2.yaml",
      task: "List files",
      stream: true,
    })
    await client.postInput(session.session_id, { content: "Continue" })
    const records = await client.readSessionRecords(session.session_id)
    const events = []
    for await (const event of streamSessionEvents(session.session_id, {
      config: { baseUrl, requestTimeoutMs: 5_000 },
    })) {
      events.push(event)
    }

    assert.equal(records.total, 1)
    const recordRows = records.records as Array<{ record: { content: string } }>
    assert.equal(recordRows[0].record.content, "Continue")
    assert.deepEqual(
      events.map((event) => event.type),
      ["completion"],
    )
  },
)
