import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

type Scenario = "smoke" | "restart" | "all"

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url))

const parseArgs = (argv: string[]): { scenario: Scenario; timeoutMs: number } => {
  const args: Record<string, string> = {}
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i] ?? ""
    if (!token.startsWith("--")) continue
    const key = token.slice(2)
    const next = argv[i + 1]
    if (!next || next.startsWith("--")) {
      args[key] = "true"
      continue
    }
    args[key] = next
    i += 1
  }
  return {
    scenario: (args["scenario"] as Scenario | undefined) ?? "all",
    timeoutMs: args["timeout-ms"] ? Number(args["timeout-ms"]) : 60_000,
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const utcStamp = (): string => {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`
}

const runPty = async (options: {
  readonly caseId: string
  readonly command: string
  readonly cwd: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
  readonly artifactDir: string
  readonly actions: (helpers: {
    term: pty.IPty
    waitForPlainIncludes: (needle: string, timeoutMs?: number) => Promise<void>
    getPlain: () => string
  }) => Promise<void>
}): Promise<{ rawPath: string; plainPath: string; resultPath: string }> => {
  await fs.promises.mkdir(options.artifactDir, { recursive: true })
  const rawPath = path.join(options.artifactDir, "pty_raw.ansi")
  const plainPath = path.join(options.artifactDir, "pty_plain.txt")
  const resultPath = path.join(options.artifactDir, "result.json")

  const rawStream = fs.createWriteStream(rawPath)
  let plainBuffer = ""
  let exited = false

  const term = pty.spawn("bash", ["-lc", options.command], {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: options.cwd,
    env: { ...process.env, BREADBOARD_ENGINE_PREFER_BUNDLE: "0" },
  })

  term.onData((chunk) => {
    rawStream.write(chunk)
    plainBuffer += stripAnsi(chunk)
    if (plainBuffer.length > 2_000_000) plainBuffer = plainBuffer.slice(-2_000_000)
  })

  term.onExit(() => {
    exited = true
  })

  const waitForPlainIncludes = async (needle: string, timeoutMs = 25_000) => {
    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      if (plainBuffer.includes(needle)) return
      if (exited) break
      await sleep(50)
    }
    throw new Error(`[${options.caseId}] timed out waiting for: ${needle}`)
  }

  const timeoutAt = Date.now() + options.timeoutMs
  while (Date.now() < timeoutAt && !exited) {
    const remaining = timeoutAt - Date.now()
    await Promise.race([
      options.actions({ term, waitForPlainIncludes, getPlain: () => plainBuffer }),
      sleep(Math.min(remaining, options.timeoutMs)),
    ])
    break
  }

  await sleep(500)
  try {
    term.kill()
  } catch {}
  rawStream.end()
  await new Promise((resolve) => rawStream.on("close", resolve))

  const rawDisk = await fs.promises.readFile(rawPath, "utf8").catch(() => "")
  const plainDisk = stripAnsi(rawDisk)
  await fs.promises.writeFile(plainPath, `${plainDisk}\n`, "utf8")

  return { rawPath, plainPath, resultPath }
}

const assert = (ok: boolean, message: string) => ({ ok, message })

const main = async () => {
  const args = parseArgs(process.argv)
  const root = path.resolve(THIS_DIR, "..", "..")
  const artifactsRoot = path.join(root, "opentui_slab", "artifacts", "phaseB_pty", utcStamp())
  await fs.promises.mkdir(artifactsRoot, { recursive: true })

  const controllerCmd = "cd ../opentui_slab && bun run phaseB/controller.ts --exit-after-ms 45000"

  const runRestart = async () => {
    const dir = path.join(artifactsRoot, "restart")
    const capture = await runPty({
      caseId: "restart",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 40,
      cols: 120,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      actions: async ({ term, waitForPlainIncludes }) => {
        await waitForPlainIncludes("Enter submit", 40_000)
        term.write("first message")
        await sleep(50)
        term.write("\r")
        await sleep(5000)
        term.write("\x12") // Ctrl+R restarts UI
        await sleep(9000)
        term.write("second message")
        await sleep(50)
        term.write("\r")
        await sleep(8000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const userCount = (plainDisk.match(/\[user]/g) || []).length
    const sessionCount = (plainDisk.match(/\[session]/g) || []).length
    const checks = [
      assert(userCount >= 2, "prints at least two user blocks"),
      assert(sessionCount === 1, "prints exactly one session banner"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(capture.resultPath, `${JSON.stringify({ pass, checks, capture }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("restart scenario failed")
    return capture
  }

  if (args.scenario === "restart" || args.scenario === "all") {
    await runRestart()
  }

  process.stdout.write(`[phaseB_pty] artifacts: ${artifactsRoot}\n`)
}

main().catch((err) => {
  console.error(`[phaseB_pty] failed: ${(err as Error).message}`)
  process.exitCode = 1
})

