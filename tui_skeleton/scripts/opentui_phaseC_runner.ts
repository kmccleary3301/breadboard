import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

type Scenario = "smoke" | "all"

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
    timeoutMs: args["timeout-ms"] ? Number(args["timeout-ms"]) : 120_000,
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
  readonly env?: Record<string, string>
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
    env: { ...process.env, ...(options.env ?? {}) },
  })

  term.onData((chunk) => {
    rawStream.write(chunk)
    plainBuffer += stripAnsi(chunk)
    if (plainBuffer.length > 3_000_000) plainBuffer = plainBuffer.slice(-3_000_000)
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
  const artifactsRoot = path.join(root, "opentui_slab", "artifacts", "phaseC_pty", utcStamp())
  await fs.promises.mkdir(artifactsRoot, { recursive: true })

  const controllerCmd =
    "cd ../opentui_slab && bun run phaseB/controller.ts --exit-after-ms 90000 --permission-mode prompt"

  const runSmoke = async () => {
    const dir = path.join(artifactsRoot, "smoke")
    const capture = await runPty({
      caseId: "smoke",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
        BREADBOARD_WORKSPACE: ".",
        BREADBOARD_DEBUG_FAKE_PERMISSION: "1",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await waitForPlainIncludes("Enter submit", 45_000)

        // 1) Command palette → save transcript (validates palette + controller command path).
        term.write("\x0b") // Ctrl+K
        await sleep(400)
        term.write("save")
        await sleep(400)
        term.write("\r")
        await waitForPlainIncludes("[transcript] saved", 20_000)

        // 2) File picker (via @) → select README-ish file → submit message containing mention.
        term.write("@")
        await sleep(400)
        term.write("README")
        await sleep(600)
        term.write("\r") // select first file
        await sleep(500)
        term.write("file mention test")
        await sleep(100)
        term.write("\r") // submit
        await waitForPlainIncludes("[user]", 12_000)

        // 3) Model picker (alt/option+p via ESC prefix) → select first match (requires session).
        term.write("\x1bp") // Option+P
        await sleep(450)
        term.write("openai")
        await sleep(700)
        term.write("\r")
        await waitForPlainIncludes("[command] set_model", 20_000)

        // 4) Permission overlay (debug injected via command palette) → allow once.
        term.write("\x0b") // Ctrl+K
        await sleep(400)
        term.write("debug")
        await sleep(500)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 20_000)
        await sleep(500)
        term.write("\r") // allow once (first option)
        await waitForPlainIncludes("[permission] decision allow-once", 20_000)

        // 5) Transcript search overlay opens/closes.
        term.write("\x0f") // Ctrl+O
        await sleep(300)
        term.write("permission")
        await sleep(600)
        term.write("\x1b") // Esc closes search overlay
        await sleep(500)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [
      assert(plainDisk.includes("[transcript] saved"), "save_transcript prints saved path"),
      assert(plainDisk.includes("[command] set_model"), "model picker triggers set_model"),
      assert(plainDisk.includes("[permission] decision allow-once"), "permission overlay can decide"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(capture.resultPath, `${JSON.stringify({ pass, checks, capture }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("phaseC smoke failed")
    return capture
  }

  if (args.scenario === "smoke" || args.scenario === "all") {
    await runSmoke()
  }

  process.stdout.write(`[phaseC_pty] artifacts: ${artifactsRoot}\n`)
}

main().catch((err) => {
  console.error(`[phaseC_pty] failed: ${(err as Error).message}`)
  process.exitCode = 1
})

