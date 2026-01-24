import fs from "node:fs"
import path from "node:path"
import net from "node:net"
import { spawn, type ChildProcess } from "node:child_process"
import { fileURLToPath } from "node:url"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url))

type RunMode = "external" | "auto"
type ScenarioId = "smoke" | "resize_storm" | "paste_torture" | "sustain" | "start_stop_loop" | "all"

type RunnerArgs = {
  readonly scenario: ScenarioId
  readonly mode: RunMode
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
}

const parseArgs = (argv: string[]): Partial<RunnerArgs> => {
  const out: Record<string, string> = {}
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i] ?? ""
    if (!token.startsWith("--")) continue
    const key = token.slice(2)
    const next = argv[i + 1]
    if (!next || next.startsWith("--")) {
      out[key] = "true"
      continue
    }
    out[key] = next
    i += 1
  }
  return {
    scenario: (out["scenario"] as ScenarioId | undefined) ?? undefined,
    mode: (out["mode"] as RunMode | undefined) ?? undefined,
    configPath: out["config"],
    artifactsRoot: out["artifacts-root"],
    rows: out["rows"] ? Number(out["rows"]) : undefined,
    cols: out["cols"] ? Number(out["cols"]) : undefined,
    timeoutMs: out["timeout-ms"] ? Number(out["timeout-ms"]) : undefined,
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const utcStamp = (): string => {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`
}

const pickEphemeralPort = (host = "127.0.0.1"): Promise<number> =>
  new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, host, () => {
      const addr = server.address()
      if (!addr || typeof addr === "string") {
        server.close(() => reject(new Error("Unable to resolve ephemeral port.")))
        return
      }
      const port = addr.port
      server.close(() => resolve(port))
    })
  })

const healthCheck = async (baseUrl: string, timeoutMs = 1500): Promise<boolean> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const url = new URL("/health", baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
    const resp = await fetch(url, { signal: controller.signal })
    return resp.ok
  } catch {
    return false
  } finally {
    clearTimeout(timeout)
  }
}

const waitForHealth = async (baseUrl: string, timeoutMs = 25_000): Promise<boolean> => {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    if (await healthCheck(baseUrl, 1000)) return true
    await sleep(250)
  }
  return false
}

const startExternalBridge = async (options: { logPath: string }): Promise<{ baseUrl: string; child: ChildProcess }> => {
  const host = "127.0.0.1"
  const python = process.env.BREADBOARD_ENGINE_PYTHON?.trim() || "python"
  const root = path.resolve(THIS_DIR, "..", "..")
  const maxAttempts = 5

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const port = await pickEphemeralPort(host)
    const baseUrl = `http://${host}:${port}`

    const child = spawn(python, ["-m", "agentic_coder_prototype.api.cli_bridge.server"], {
      cwd: root,
      env: {
        ...process.env,
        BREADBOARD_CLI_HOST: host,
        BREADBOARD_CLI_PORT: String(port),
        BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
      },
      stdio: ["ignore", "pipe", "pipe"],
    })

    const logStream = fs.createWriteStream(options.logPath, { flags: "a" })
    logStream.write(`\n[phaseA] bridge attempt ${attempt}/${maxAttempts} baseUrl=${baseUrl}\n`)
    child.stdout?.on("data", (chunk) => logStream.write(chunk))
    child.stderr?.on("data", (chunk) => logStream.write(chunk))
    child.once("exit", () => logStream.end())

    const exitedEarly = new Promise<boolean>((resolve) => {
      child.once("exit", () => resolve(true))
    })

    const ready = await Promise.race([waitForHealth(baseUrl, 40_000), exitedEarly.then(() => false)])
    if (ready) {
      return { baseUrl, child }
    }

    try {
      child.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
    } catch {}
    await sleep(250)

    if (attempt === maxAttempts) {
      throw new Error(`Bridge failed to become healthy after ${maxAttempts} attempts.`)
    }
  }

  throw new Error("Bridge spawn retry loop ended unexpectedly.")
}

type PtyCapture = {
  readonly rawPath: string
  readonly plainPath: string
  readonly metaPath: string
  readonly outBytes: number
  readonly exitCode: number | null
  readonly signal: number | null
}

const runPtyScenario = async (options: {
  readonly caseId: string
  readonly command: string
  readonly cwd: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
  readonly actions: (helpers: { term: pty.IPty; waitForPlainIncludes: (needle: string, timeoutMs?: number) => Promise<void> }) => Promise<void>
  readonly artifactDir: string
}): Promise<PtyCapture> => {
  await fs.promises.mkdir(options.artifactDir, { recursive: true })
  const rawPath = path.join(options.artifactDir, "pty_raw.ansi")
  const plainPath = path.join(options.artifactDir, "pty_plain.txt")
  const metaPath = path.join(options.artifactDir, "run_meta.json")

  const rawStream = fs.createWriteStream(rawPath)
  let rawBuffer = ""
  let plainBuffer = ""
  let exitCode: number | null = null
  let signal: number | null = null

  const term = pty.spawn("bash", ["-lc", options.command], {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: options.cwd,
    env: { ...process.env },
  })

  term.onData((chunk) => {
    rawStream.write(chunk)
    rawBuffer += chunk
    if (rawBuffer.length > 2_000_000) rawBuffer = rawBuffer.slice(-2_000_000)
    plainBuffer += stripAnsi(chunk)
    if (plainBuffer.length > 2_000_000) plainBuffer = plainBuffer.slice(-2_000_000)
  })

  term.onExit((e) => {
    exitCode = e.exitCode
    signal = e.signal
  })

  const startedAt = new Date().toISOString()
  const deadline = Date.now() + options.timeoutMs

  const waitForPlainIncludes = async (needle: string, timeoutMs = 25_000): Promise<void> => {
    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      if (plainBuffer.includes(needle)) return
      if (exitCode !== null) break
      await sleep(50)
    }
    throw new Error(`[${options.caseId}] Timed out waiting for text: ${needle}`)
  }

  const timeoutPromise = (async () => {
    while (Date.now() < deadline && exitCode === null) {
      await sleep(50)
    }
    if (exitCode === null) {
      try {
        term.kill()
      } catch {}
    }
  })()

  await Promise.race([options.actions({ term, waitForPlainIncludes }), timeoutPromise])
  await sleep(500)
  try {
    term.kill()
  } catch {}

  rawStream.end()
  await new Promise((resolve) => rawStream.on("close", resolve))

  const rawDisk = await fs.promises.readFile(rawPath, "utf8").catch(() => rawBuffer)
  const plain = stripAnsi(rawDisk)
  await fs.promises.writeFile(plainPath, `${plain}\n`, "utf8")

  const finishedAt = new Date().toISOString()
  const meta = {
    caseId: options.caseId,
    command: options.command,
    cwd: options.cwd,
    rows: options.rows,
    cols: options.cols,
    startedAt,
    finishedAt,
    timeoutMs: options.timeoutMs,
    exitCode,
    signal,
  }
  await fs.promises.writeFile(metaPath, `${JSON.stringify(meta, null, 2)}\n`, "utf8")

  return {
    rawPath,
    plainPath,
    metaPath,
    outBytes: Buffer.byteLength(rawDisk),
    exitCode,
    signal,
  }
}

const writePhaseAResult = async (artifactDir: string, payload: unknown) => {
  await fs.promises.writeFile(path.join(artifactDir, "phaseA_result.json"), `${JSON.stringify(payload, null, 2)}\n`, "utf8")
}

const assertInvariant = (ok: boolean, message: string): { ok: boolean; message: string } => ({ ok, message })

const hasAny = (value: string, needles: readonly string[]): boolean => needles.some((needle) => value.includes(needle))

const defaultExitModeChecks = (raw: string) => [
  assertInvariant(raw.includes("?2004l") || raw.includes("[?2004l"), "bracketed paste disabled on exit (heuristic)"),
  assertInvariant(raw.includes("?1000l") || raw.includes("[?1000l"), "mouse mode disabled on exit (heuristic)"),
  assertInvariant(raw.includes("?1002l") || raw.includes("[?1002l"), "mouse drag mode disabled on exit (heuristic)"),
  assertInvariant(raw.includes("?1006l") || raw.includes("[?1006l"), "mouse sgr mode disabled on exit (heuristic)"),
]

const defaultCrashSignatureChecks = (plain: string, raw: string) => [
  assertInvariant(
    !hasAny(plain, ["Traceback (most recent call last)", "AttributeError", "RangeError", "ReferenceError", "TypeError"]),
    "no obvious exceptions in plain text",
  ),
  assertInvariant(
    !hasAny(raw, ["Traceback (most recent call last)", "AttributeError", "RangeError", "ReferenceError", "TypeError"]),
    "no obvious exceptions in raw output",
  ),
  assertInvariant(!hasAny(raw, ["NaN", "EBADF", "EADDRINUSE"]), "no obvious terminal/port crash signatures in raw output"),
]

const runSmoke = async (ctx: {
  readonly mode: RunMode
  readonly baseUrl?: string
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
}): Promise<void> => {
  const root = path.resolve(THIS_DIR, "..", "..")
  const cwd = path.join(root, "tui_skeleton")
  const slabCwd = "../opentui_slab"
  const baseArgs = [`--config ${ctx.configPath}`]
  if (ctx.mode === "external") {
    baseArgs.push(`--no-start-engine --base-url ${ctx.baseUrl}`)
  }
  const cmd = `cd ${slabCwd} && bun run index.ts ${baseArgs.join(" ")}`

  const artifactDir = path.join(ctx.artifactsRoot, "smoke")
  const capture = await runPtyScenario({
    caseId: "smoke",
    command: cmd,
    cwd,
    rows: ctx.rows,
    cols: ctx.cols,
    timeoutMs: ctx.timeoutMs,
    artifactDir,
    actions: async ({ term, waitForPlainIncludes }) => {
      await waitForPlainIncludes("Enter to submit", 40_000)
      term.write("Hello from PhaseA smoke")
      await sleep(50)
      term.write("\r")
      await sleep(10_000)
      term.write("\x04")
      await sleep(1500)
    },
  })

  const plain = await fs.promises.readFile(capture.plainPath, "utf8")
  const raw = await fs.promises.readFile(capture.rawPath, "utf8")
  const checks = [
    assertInvariant(plain.includes("BreadBoard OpenTUI slab"), "footer appears at least once"),
    assertInvariant(plain.includes("[user]"), "user echo printed"),
    assertInvariant(plain.includes("[session]"), "session id printed"),
    ...defaultExitModeChecks(raw),
    ...defaultCrashSignatureChecks(plain, raw),
  ]
  const pass = checks.every((c) => c.ok)
  await writePhaseAResult(artifactDir, { pass, checks, capture })
  if (!pass) {
    throw new Error(`smoke failed: ${checks.filter((c) => !c.ok).map((c) => c.message).join(", ")}`)
  }
}

const runResizeStorm = async (ctx: {
  readonly mode: RunMode
  readonly baseUrl?: string
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
}): Promise<void> => {
  const root = path.resolve(THIS_DIR, "..", "..")
  const cwd = path.join(root, "tui_skeleton")
  const slabCwd = "../opentui_slab"
  const baseArgs = [`--config ${ctx.configPath}`, "--local-spam --local-spam-hz 80"]
  if (ctx.mode === "external") {
    baseArgs.push(`--no-start-engine --base-url ${ctx.baseUrl}`)
  }
  const cmd = `cd ${slabCwd} && bun run index.ts ${baseArgs.join(" ")}`

  const artifactDir = path.join(ctx.artifactsRoot, "resize_storm")
  const capture = await runPtyScenario({
    caseId: "resize_storm",
    command: cmd,
    cwd,
    rows: ctx.rows,
    cols: ctx.cols,
    timeoutMs: ctx.timeoutMs,
    artifactDir,
    actions: async ({ term, waitForPlainIncludes }) => {
      await waitForPlainIncludes("Enter to submit", 40_000)
      term.write("Resize storm prompt")
      await sleep(50)
      term.write("\r")

      const started = Date.now()
      const durationMs = 12_000
      while (Date.now() - started < durationMs) {
        const cols = Math.max(50, Math.floor(80 + Math.random() * 100))
        const rows = Math.max(18, Math.floor(24 + Math.random() * 30))
        term.resize(cols, rows)
        if (Math.random() < 0.15) {
          term.write("x")
        }
        await sleep(30)
      }

      await sleep(1500)
      term.write("\x04")
      await sleep(1500)
    },
  })

  const plain = await fs.promises.readFile(capture.plainPath, "utf8")
  const raw = await fs.promises.readFile(capture.rawPath, "utf8")
  const checks = [
    assertInvariant(plain.includes("BreadBoard OpenTUI slab"), "footer appears at least once"),
    assertInvariant(plain.includes("[user]"), "user echo printed"),
    assertInvariant(plain.includes("[session]"), "session id printed"),
    ...defaultExitModeChecks(raw),
    ...defaultCrashSignatureChecks(plain, raw),
  ]
  const pass = checks.every((c) => c.ok)
  await writePhaseAResult(artifactDir, { pass, checks, capture })
  if (!pass) {
    throw new Error(`resize_storm failed: ${checks.filter((c) => !c.ok).map((c) => c.message).join(", ")}`)
  }
}

const runPasteTorture = async (ctx: {
  readonly mode: RunMode
  readonly baseUrl?: string
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
}): Promise<void> => {
  const root = path.resolve(THIS_DIR, "..", "..")
  const cwd = path.join(root, "tui_skeleton")
  const slabCwd = "../opentui_slab"
  const baseArgs = [`--config ${ctx.configPath}`]
  if (ctx.mode === "external") {
    baseArgs.push(`--no-start-engine --base-url ${ctx.baseUrl}`)
  }
  const cmd = `cd ${slabCwd} && bun run index.ts ${baseArgs.join(" ")}`

  const artifactDir = path.join(ctx.artifactsRoot, "paste_torture")
  const capture = await runPtyScenario({
    caseId: "paste_torture",
    command: cmd,
    cwd,
    rows: ctx.rows,
    cols: ctx.cols,
    timeoutMs: ctx.timeoutMs,
    artifactDir,
    actions: async ({ term, waitForPlainIncludes }) => {
      await waitForPlainIncludes("Enter to submit", 40_000)

      const mkPayload = (bytes: number) => {
        const line = "0123456789abcdef".repeat(16) + "\n"
        const lines = Math.max(1, Math.floor(bytes / line.length))
        return Array.from({ length: lines }, (_, i) => `line ${i + 1}: ${line}`).join("")
      }

      const paste = (text: string) => {
        term.write("\x1b[200~")
        term.write(text)
        term.write("\x1b[201~")
      }

      paste("small paste\n")
      await sleep(300)
      paste(mkPayload(10_000))
      await sleep(600)
      paste(mkPayload(120_000))
      await sleep(600)

      term.write("After paste prompt")
      await sleep(50)
      term.write("\r")
      await sleep(4000)
      term.write("\x04")
      await sleep(1500)
    },
  })

  const plain = await fs.promises.readFile(capture.plainPath, "utf8")
  const raw = await fs.promises.readFile(capture.rawPath, "utf8")
  const checks = [
    assertInvariant(plain.includes("BreadBoard OpenTUI slab"), "footer appears at least once"),
    assertInvariant(plain.includes("[paste]"), "paste events logged"),
    assertInvariant(plain.includes("[user]"), "user echo printed"),
    assertInvariant(plain.includes("[session]"), "session id printed"),
    ...defaultExitModeChecks(raw),
    ...defaultCrashSignatureChecks(plain, raw),
  ]
  const pass = checks.every((c) => c.ok)
  await writePhaseAResult(artifactDir, { pass, checks, capture })
  if (!pass) {
    throw new Error(`paste_torture failed: ${checks.filter((c) => !c.ok).map((c) => c.message).join(", ")}`)
  }
}

const runSustain = async (ctx: {
  readonly mode: RunMode
  readonly baseUrl?: string
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
  readonly timeoutMs: number
}): Promise<void> => {
  const root = path.resolve(THIS_DIR, "..", "..")
  const cwd = path.join(root, "tui_skeleton")
  const slabCwd = "../opentui_slab"
  const baseArgs = [`--config ${ctx.configPath}`, "--local-spam --local-spam-hz 120"]
  if (ctx.mode === "external") {
    baseArgs.push(`--no-start-engine --base-url ${ctx.baseUrl}`)
  }
  const cmd = `cd ${slabCwd} && bun run index.ts ${baseArgs.join(" ")}`

  const artifactDir = path.join(ctx.artifactsRoot, "sustain")
  const capture = await runPtyScenario({
    caseId: "sustain",
    command: cmd,
    cwd,
    rows: ctx.rows,
    cols: ctx.cols,
    timeoutMs: ctx.timeoutMs,
    artifactDir,
    actions: async ({ term, waitForPlainIncludes }) => {
      await waitForPlainIncludes("Enter to submit", 40_000)
      const started = Date.now()
      const durationMs = 12_000
      while (Date.now() - started < durationMs) {
        term.write("typing ")
        await sleep(120)
      }
      term.write("\r")
      await sleep(3000)
      term.write("\x04")
      await sleep(1500)
    },
  })

  const plain = await fs.promises.readFile(capture.plainPath, "utf8")
  const raw = await fs.promises.readFile(capture.rawPath, "utf8")
  const checks = [
    assertInvariant(plain.includes("BreadBoard OpenTUI slab"), "footer appears at least once"),
    assertInvariant(plain.includes("[local] spam"), "local spam produced output"),
    ...defaultExitModeChecks(raw),
    ...defaultCrashSignatureChecks(plain, raw),
  ]
  const pass = checks.every((c) => c.ok)
  await writePhaseAResult(artifactDir, { pass, checks, capture })
  if (!pass) {
    throw new Error(`sustain failed: ${checks.filter((c) => !c.ok).map((c) => c.message).join(", ")}`)
  }
}

const runStartStopLoop = async (ctx: {
  readonly mode: RunMode
  readonly baseUrl?: string
  readonly configPath: string
  readonly artifactsRoot: string
  readonly rows: number
  readonly cols: number
}): Promise<void> => {
  const root = path.resolve(THIS_DIR, "..", "..")
  const cwd = path.join(root, "tui_skeleton")
  const slabCwd = "../opentui_slab"
  const baseArgs = [`--config ${ctx.configPath}`]
  if (ctx.mode === "external") {
    baseArgs.push(`--no-start-engine --base-url ${ctx.baseUrl}`)
  }
  const cmd = `cd ${slabCwd} && bun run index.ts ${baseArgs.join(" ")}`

  const artifactDir = path.join(ctx.artifactsRoot, "start_stop_loop")
  await fs.promises.mkdir(artifactDir, { recursive: true })

  const iterations = 8
  const results: Array<{ iter: number; pass: boolean; checks: { ok: boolean; message: string }[]; capture: PtyCapture }> = []
  for (let i = 1; i <= iterations; i += 1) {
    const iterDir = path.join(artifactDir, `iter_${String(i).padStart(2, "0")}`)
    const capture = await runPtyScenario({
      caseId: `start_stop_loop_${i}`,
      command: cmd,
      cwd,
      rows: ctx.rows,
      cols: ctx.cols,
      timeoutMs: 20_000,
      artifactDir: iterDir,
      actions: async ({ term, waitForPlainIncludes }) => {
        await waitForPlainIncludes("Enter to submit", 40_000)
        term.write("\x04")
        await sleep(800)
      },
    })
    const raw = await fs.promises.readFile(capture.rawPath, "utf8")
    const checks = [...defaultExitModeChecks(raw), ...defaultCrashSignatureChecks("", raw)]
    const pass = checks.every((c) => c.ok)
    results.push({ iter: i, pass, checks, capture })
    if (!pass) break
  }

  const pass = results.length === iterations && results.every((r) => r.pass)
  await writePhaseAResult(artifactDir, { pass, iterations, results })
  if (!pass) {
    throw new Error("start_stop_loop failed")
  }
}

const main = async () => {
  const parsed = parseArgs(process.argv)
  const args: RunnerArgs = {
    scenario: (parsed.scenario ?? "all") as ScenarioId,
    mode: (parsed.mode ?? "external") as RunMode,
    configPath: parsed.configPath ?? "agent_configs/codex_cli_gpt51mini_e4_live.yaml",
    artifactsRoot:
      parsed.artifactsRoot ??
      path.resolve(THIS_DIR, "..", "..", "opentui_slab", "artifacts", "phaseA", utcStamp()),
    rows: Number.isFinite(parsed.rows) && (parsed.rows as number) > 0 ? (parsed.rows as number) : 40,
    cols: Number.isFinite(parsed.cols) && (parsed.cols as number) > 0 ? (parsed.cols as number) : 120,
    timeoutMs: Number.isFinite(parsed.timeoutMs) && (parsed.timeoutMs as number) > 0 ? (parsed.timeoutMs as number) : 40_000,
  }

  await fs.promises.mkdir(args.artifactsRoot, { recursive: true })

  let bridge: { baseUrl: string; child: ChildProcess } | null = null
  if (args.mode === "external") {
    bridge = await startExternalBridge({ logPath: path.join(args.artifactsRoot, "bridge.log") })
    await fs.promises.writeFile(
      path.join(args.artifactsRoot, "bridge_meta.json"),
      `${JSON.stringify({ baseUrl: bridge.baseUrl, pid: bridge.child.pid }, null, 2)}\n`,
      "utf8",
    )
  }

  const ctx = {
    mode: args.mode,
    baseUrl: bridge?.baseUrl,
    configPath: args.configPath,
    artifactsRoot: args.artifactsRoot,
    rows: args.rows,
    cols: args.cols,
    timeoutMs: args.timeoutMs,
  }

  try {
    if (args.scenario === "smoke" || args.scenario === "all") await runSmoke(ctx)
    if (args.scenario === "resize_storm" || args.scenario === "all") await runResizeStorm(ctx)
    if (args.scenario === "paste_torture" || args.scenario === "all") await runPasteTorture(ctx)
    if (args.scenario === "sustain" || args.scenario === "all") await runSustain(ctx)
    if (args.scenario === "start_stop_loop" || args.scenario === "all") await runStartStopLoop(ctx)
  } finally {
    if (bridge) {
      try {
        bridge.child.kill(process.platform === "win32" ? "SIGTERM" : "SIGINT")
      } catch {}
    }
  }

  const scenarioDirs: Record<string, string> = {
    smoke: path.join(args.artifactsRoot, "smoke"),
    resize_storm: path.join(args.artifactsRoot, "resize_storm"),
    paste_torture: path.join(args.artifactsRoot, "paste_torture"),
    sustain: path.join(args.artifactsRoot, "sustain"),
    start_stop_loop: path.join(args.artifactsRoot, "start_stop_loop"),
  }

  const loadResult = async (dir: string) => {
    const p = path.join(dir, "phaseA_result.json")
    const raw = await fs.promises.readFile(p, "utf8").catch(() => "")
    if (!raw) return null
    try {
      return JSON.parse(raw) as { pass?: boolean }
    } catch {
      return null
    }
  }

  const includedScenarios: string[] =
    args.scenario === "all"
      ? ["smoke", "resize_storm", "paste_torture", "sustain", "start_stop_loop"]
      : [args.scenario]

  const scenarioResults: Record<string, unknown> = {}
  let overallPass = true
  for (const scenario of includedScenarios) {
    const dir = scenarioDirs[scenario]
    const result = dir ? await loadResult(dir) : null
    scenarioResults[scenario] = result
    if (!result || result.pass !== true) {
      overallPass = false
    }
  }

  const suiteSummary: Record<string, unknown> = {
    scenario: args.scenario,
    mode: args.mode,
    configPath: args.configPath,
    artifactsRoot: args.artifactsRoot,
    bridgeBaseUrl: bridge?.baseUrl ?? null,
    pass: overallPass,
    results: scenarioResults,
  }
  await fs.promises.writeFile(path.join(args.artifactsRoot, "suite_summary.json"), `${JSON.stringify(suiteSummary, null, 2)}\n`, "utf8")

  process.stdout.write(`[phaseA] artifacts: ${args.artifactsRoot}\n`)
}

main().catch((error) => {
  console.error(`[phaseA] runner failed: ${(error as Error).message}`)
  process.exitCode = 1
})

