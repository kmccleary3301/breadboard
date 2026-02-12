import { promises as fs } from "node:fs"
import path from "node:path"
import { createSession, postInput, streamSessionEvents } from "../../opentui_slab/phaseB/bridge.ts"
import { formatBridgeEventForStdout } from "../../opentui_slab/phaseB/format.ts"

const args = process.argv.slice(2)
const outputArg = args[0]
const outputPath = outputArg && outputArg.trim().length > 0 ? outputArg : "opentui_canonical_capture.txt"
const baseUrl = process.env.BREADBOARD_API_URL?.trim() || "http://127.0.0.1:9191"

const run = async () => {
  const session = await createSession(baseUrl, {
    config_path: "mock",
    task: "",
    stream: true,
  })

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 20_000)
  const output: string[] = []

  const streamTask = (async () => {
    for await (const evt of streamSessionEvents(baseUrl, session.session_id, { signal: controller.signal })) {
      const rendered = formatBridgeEventForStdout(evt as unknown as Record<string, unknown>)
      if (rendered) output.push(rendered)
      if (evt.type === "run.end") break
    }
  })()

  await postInput(baseUrl, session.session_id, { content: "start" })

  try {
    await streamTask
  } finally {
    clearTimeout(timeout)
  }

  const resolved = path.isAbsolute(outputPath) ? outputPath : path.resolve(process.cwd(), outputPath)
  await fs.writeFile(resolved, output.join(""), "utf8")
  console.log(`OpenTUI capture written to ${resolved}`)
}

run().catch((err) => {
  console.error(`[opentui_canonical_capture] failed: ${(err as Error).message}`)
  process.exitCode = 1
})
