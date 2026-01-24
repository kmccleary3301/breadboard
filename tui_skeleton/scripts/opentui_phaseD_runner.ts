import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

import { checkTerminalInvariants } from "./harness/terminal_invariants.ts"

type Scenario =
  | "smoke"
  | "permission_variants"
  | "palette_commands"
  | "file_picker_fuzzy"
  | "model_picker_filter"
  | "draft_persistence"
  | "slash_help"
  | "resize_storm"
  | "resize_storm_streaming"
  | "all"

type EnvFlavor = "plain" | "tmux_like"

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url))

const parseArgs = (
  argv: string[],
): {
  scenario: Scenario
  timeoutMs: number
  updateGoldens: boolean
  goldensDir?: string
  smokeFlavor: "plain" | "tmux_like" | "both"
} => {
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
  const scenarioRaw = (args["scenario"] ?? "all").trim()
  const scenario: Scenario =
    scenarioRaw === "smoke" ||
    scenarioRaw === "permission_variants" ||
    scenarioRaw === "palette_commands" ||
    scenarioRaw === "file_picker_fuzzy" ||
    scenarioRaw === "model_picker_filter" ||
    scenarioRaw === "draft_persistence" ||
    scenarioRaw === "slash_help" ||
    scenarioRaw === "resize_storm" ||
    scenarioRaw === "resize_storm_streaming" ||
    scenarioRaw === "all"
      ? (scenarioRaw as Scenario)
      : "all"
  const smokeFlavorRaw = (args["smoke-flavor"] ?? "both").trim().toLowerCase()
  const smokeFlavor: "plain" | "tmux_like" | "both" =
    smokeFlavorRaw === "plain" || smokeFlavorRaw === "tmux_like" || smokeFlavorRaw === "both"
      ? (smokeFlavorRaw as "plain" | "tmux_like" | "both")
      : "both"
  return {
    scenario,
    timeoutMs: args["timeout-ms"] ? Number(args["timeout-ms"]) : 180_000,
    updateGoldens: ["true", "1", "yes"].includes((args["update-goldens"] ?? "").trim().toLowerCase()),
    goldensDir: args["goldens-dir"]?.trim() || undefined,
    smokeFlavor,
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const utcStamp = (): string => {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`
}

type ReadyEvent = { readonly ts?: number; readonly label: string; readonly [k: string]: unknown }

const readReadyEvents = async (pathValue: string): Promise<ReadyEvent[]> => {
  const raw = await fs.promises.readFile(pathValue, "utf8").catch(() => "")
  if (!raw.trim()) return []
  const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean)
  const events: ReadyEvent[] = []
  for (const line of lines) {
    try {
      const parsed = JSON.parse(line) as ReadyEvent
      if (parsed && typeof parsed.label === "string") events.push(parsed)
    } catch {
      // ignore bad lines
    }
  }
  return events
}

const waitForReadyLabel = async (pathValue: string, label: string, timeoutMs = 25_000) => {
  const started = Date.now()
  while (Date.now() - started < timeoutMs) {
    const events = await readReadyEvents(pathValue)
    if (events.some((evt) => evt.label === label)) return
    await sleep(75)
  }
  throw new Error(`[ready] timed out waiting for ${label}`)
}

const assert = (ok: boolean, message: string) => ({ ok, message })

const main = async () => {
  const args = parseArgs(process.argv)
  const root = path.resolve(THIS_DIR, "..", "..")
  const artifactsBaseRaw = (process.env.BB_PHASED_PTY_ARTIFACTS_BASE ?? "").trim()
  const artifactsBase = artifactsBaseRaw
    ? path.isAbsolute(artifactsBaseRaw)
      ? artifactsBaseRaw
      : path.resolve(root, artifactsBaseRaw)
    : path.resolve(root, "..", "docs_tmp", "cli_phase_3", "artifacts", "opentui_phaseD_pty")
  const artifactsRoot = path.join(artifactsBase, utcStamp())
  await fs.promises.mkdir(artifactsRoot, { recursive: true })

  const controllerCmd =
    "cd ../opentui_slab && bun run phaseB/controller.ts --exit-after-ms 25000 --permission-mode prompt"

  const runPty = async (options: {
    readonly caseId: string
    readonly flavor: EnvFlavor
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
  }): Promise<{ artifactDir: string; rawPath: string; plainPath: string; resultPath: string }> => {
    await fs.promises.mkdir(options.artifactDir, { recursive: true })
    const rawPath = path.join(options.artifactDir, "pty_raw.ansi")
    const plainPath = path.join(options.artifactDir, "pty_plain.txt")
    const resultPath = path.join(options.artifactDir, "result.json")
    const readyPath = path.join(options.artifactDir, "controller_ready.ndjson")

    const rawStream = fs.createWriteStream(rawPath)
    let plainBuffer = ""
    let exited = false
    let exitCode: number | null = null
    let exitSignal: number | null = null
    let actionsError: string | null = null
    let invariantsError: string | null = null
    let forcedKill = false

    const baseEnv: Record<string, string> = {
      BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
      BREADBOARD_WORKSPACE: ".",
      BREADBOARD_CONTROLLER_READY_FILE: readyPath,
    }
    const flavorEnv: Record<string, string> =
      options.flavor === "tmux_like"
        ? {
            TERM: "screen-256color",
            TMUX: "/tmp/tmux-fake",
          }
        : {}

    const term = pty.spawn("bash", ["-lc", options.command], {
      name: "xterm-256color",
      cols: options.cols,
      rows: options.rows,
      cwd: options.cwd,
      env: { ...process.env, ...baseEnv, ...flavorEnv, ...(options.env ?? {}) },
    })

    term.onData((chunk) => {
      rawStream.write(chunk)
      plainBuffer += stripAnsi(chunk)
      if (plainBuffer.length > 3_000_000) plainBuffer = plainBuffer.slice(-3_000_000)
    })

    term.onExit((evt) => {
      exited = true
      exitCode = typeof evt.exitCode === "number" ? evt.exitCode : null
      exitSignal = typeof evt.signal === "number" ? evt.signal : null
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

    try {
      await waitForReadyLabel(readyPath, "bridge_ready", 25_000)
      await waitForReadyLabel(readyPath, "ui_connected", 25_000)

      const timeoutAt = Date.now() + options.timeoutMs
      while (Date.now() < timeoutAt && !exited) {
        const remaining = timeoutAt - Date.now()
        await Promise.race([options.actions({ term, waitForPlainIncludes, getPlain: () => plainBuffer }), sleep(remaining)])
        break
      }
    } catch (err) {
      actionsError = (err as Error).message
    } finally {
      // Request a clean shutdown first so terminal mode cleanup can run.
      try {
        term.write("\u0004") // Ctrl+D (exit)
      } catch {}

      const exitDeadline = Date.now() + 28_000
      while (!exited && Date.now() < exitDeadline) {
        await sleep(50)
      }

      try {
        term.write("\u0003") // Ctrl+C
      } catch {}

      const shutdownStarted = Date.now()
      while (!exited && Date.now() - shutdownStarted < 7_000) {
        await sleep(50)
      }

      if (!exited) {
        try {
          term.kill()
          forcedKill = true
        } catch {}
      }

      rawStream.end()
      await new Promise((resolve) => rawStream.on("close", resolve))

      const rawDisk = await fs.promises.readFile(rawPath, "utf8").catch(() => "")
      const plainDisk = stripAnsi(rawDisk)
      await fs.promises.writeFile(plainPath, `${plainDisk}\n`, "utf8")
      const invariants = checkTerminalInvariants(rawDisk)
      await fs.promises.writeFile(
        path.join(options.artifactDir, "terminal_invariants.json"),
        `${JSON.stringify(invariants, null, 2)}\n`,
        "utf8",
      )
      if (!invariants.pass) {
        invariantsError = `terminal invariants failed (${options.caseId})`
      }
      await fs.promises.writeFile(
        resultPath,
        `${JSON.stringify(
          {
            caseId: options.caseId,
            flavor: options.flavor,
            actionsError,
            invariantsError,
            forcedKill,
            exited,
            exitCode,
            exitSignal,
            rawPath,
            plainPath,
            readyPath,
          },
          null,
          2,
        )}\n`,
        "utf8",
      )
    }

    if (actionsError) {
      throw new Error(actionsError)
    }
    if (invariantsError) {
      throw new Error(invariantsError)
    }
    if (forcedKill) {
      throw new Error(`[${options.caseId}] forced kill required for shutdown`)
    }

    return { artifactDir: options.artifactDir, rawPath, plainPath, resultPath }
  }

  const runSmoke = async (flavor: EnvFlavor) => {
    const caseId = `smoke_${flavor}`
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor,
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "1",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("\x0b") // Ctrl+K
        await sleep(400)
        term.write("save")
        await sleep(500)
        term.write("\r")
        await waitForPlainIncludes("[transcript] saved", 25_000)

        term.write("@")
        await sleep(450)
        term.write("README")
        await sleep(950)
        term.write("\r")
        await sleep(650)
        term.write("file mention test")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[user]", 25_000)

        term.write("\x1bp")
        await sleep(450)
        term.write("openai")
        await sleep(950)
        term.write("\r")
        await waitForPlainIncludes("[command] set_model", 25_000)

        term.write("\x0b")
        await sleep(450)
        term.write("debug")
        await sleep(750)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 25_000)
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("[permission] decision allow-once", 25_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const normalized = plainDisk.replace(/\r/g, "")
    const checks = [
      assert(normalized.includes("[transcript] saved"), "save_transcript prints saved path"),
      assert(normalized.includes("[command] set_model"), "model picker triggers set_model"),
      assert(normalized.includes("[permission] decision allow-once"), "permission decision printed"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error(`${caseId} failed`)
  }

  const runPermissionVariants = async () => {
    const caseId = "permission_variants"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "1",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("\x0b")
        await sleep(350)
        term.write("debug")
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 25_000)
        await waitForPlainIncludes("Tool: debug_tool", 25_000)
        await waitForPlainIncludes("preview):", 25_000)
        await sleep(500)

        term.write("r")
        await waitForPlainIncludes("[permission] decision deny-once", 25_000)

        term.write("\x0b")
        await sleep(350)
        term.write("debug")
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 25_000)
        await sleep(500)
        term.write("\x1b[B")
        await sleep(120)
        term.write("\r")
        await waitForPlainIncludes("[permission] decision allow-always", 25_000)

        term.write("\x0b")
        await sleep(350)
        term.write("debug")
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 25_000)
        await sleep(500)
        term.write("\x1b[B")
        await sleep(80)
        term.write("\x1b[B")
        await sleep(80)
        term.write("\x1b[B")
        await sleep(120)
        term.write("\r")
        await waitForPlainIncludes("[permission] decision deny-always", 25_000)

        term.write("\x0b")
        await sleep(350)
        term.write("debug")
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("[permission] request_id=debug-", 25_000)
        await sleep(500)
        term.write("s")
        await waitForPlainIncludes("[permission] decision deny-stop", 25_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [
      assert(plainDisk.includes("[permission] decision deny-once"), "deny-once is printable via modal shortcut"),
      assert(
        plainDisk.includes("[permission] decision allow-always") &&
          plainDisk.includes("scope=session") &&
          plainDisk.includes("rule=shell:*"),
        "allow-always includes scope/rule",
      ),
      assert(
        plainDisk.includes("[permission] decision deny-always") &&
          plainDisk.includes("scope=session") &&
          plainDisk.includes("rule=shell:*"),
        "deny-always includes scope/rule",
      ),
      assert(plainDisk.includes("[permission] decision deny-stop"), "deny-stop is printable via modal shortcut"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("permission variants failed")
  }

  const runPaletteCommands = async () => {
    const caseId = "palette_commands"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("hello")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[session]", 45_000)

        term.write("\x0b")
        await sleep(300)
        term.write("status")
        await sleep(450)
        term.write("\r")
        await waitForPlainIncludes("[command] status", 25_000)

        term.write("\x0b")
        await sleep(300)
        term.write("retry")
        await sleep(450)
        term.write("\r")
        await waitForPlainIncludes("[command] retry", 25_000)

        term.write("\x0b")
        await sleep(300)
        term.write("stop")
        await sleep(450)
        term.write("\r")
        await waitForPlainIncludes("[command] stop", 25_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [
      assert(plainDisk.includes("[command] status"), "status is printable from commands palette"),
      assert(plainDisk.includes("[command] retry"), "retry is printable from commands palette"),
      assert(plainDisk.includes("[command] stop"), "stop is printable from commands palette"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("palette commands failed")
  }

  const runFilePickerFuzzy = async () => {
    const caseId = "file_picker_fuzzy"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
        BREADBOARD_FILE_PICKER_WARN_BYTES: "1",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("@")
        await sleep(350)
        await waitForPlainIncludes("Files", 25_000)
        term.write("README")
        await sleep(900)
        await waitForPlainIncludes("large", 25_000)
        term.write("\r")
        await sleep(600)
        term.write("fuzzy mention")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[user]", 25_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const normalized = plainDisk.replace(/\r/g, "")
    const checks = [
      assert(normalized.includes("large"), "file picker surfaces gating/truncation hint (large)"),
      assert(normalized.includes("\n[user]\n@") || normalized.includes("[user]\n@"), "submission includes @mention"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("file picker fuzzy failed")
  }

  const runModelPickerFilter = async () => {
    const caseId = "model_picker_filter"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("hello")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[session]", 45_000)

        term.write("\x1bp")
        await sleep(450)
        term.write("provider:open gpt")
        await sleep(900)
        term.write("\x1b[B")
        await sleep(150)
        term.write("\r")
        await waitForPlainIncludes("[command] set_model", 25_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [assert(plainDisk.includes("[command] set_model"), "set_model printed after filtered selection")]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("model picker filter failed")
  }

  const runDraftPersistence = async () => {
    const caseId = "draft_persistence"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("draft persists across ui restart")
        await sleep(250)
        term.write("\x0b")
        await sleep(350)
        term.write("\x1b") // Esc
        await sleep(300)

        term.write("\x12") // Ctrl+R (restart UI)
        await sleep(2000)

        term.write("\r") // submit draft
        await waitForPlainIncludes("[user]", 45_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [
      assert(
        plainDisk.includes("draft persists across ui restart"),
        "draft survives overlay + UI restart and is submitted",
      ),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("draft persistence failed")
  }

  const runSlashHelp = async () => {
    const caseId = "slash_help"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 42,
      cols: 140,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("/")
        await sleep(300)
        term.write("help")
        await sleep(650)
        term.write("\r")
        await waitForPlainIncludes("Help / keybinds", 25_000)
        await sleep(450)
        term.write("\x1b") // Esc
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [assert(plainDisk.includes("Help / keybinds"), "help overlay visible via / command palette")]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("slash help failed")
  }

  const runResizeStorm = async () => {
    const caseId = "resize_storm"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 28,
      cols: 100,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)
        term.write("\x0b")
        await sleep(250)

        for (let i = 0; i < 30; i += 1) {
          const cols = i % 2 === 0 ? 90 : 140
          const rows = i % 2 === 0 ? 26 : 44
          term.resize(cols, rows)
          await sleep(60)
        }

        term.write("\x1b") // Esc
        await sleep(250)
        term.write("resize ok")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[user]", 45_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [assert(plainDisk.includes("resize ok"), "UI survives resize storm and can submit")]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("resize storm failed")
  }

  const runResizeStormStreaming = async () => {
    const caseId = "resize_storm_streaming"
    const dir = path.join(artifactsRoot, caseId)
    const capture = await runPty({
      caseId,
      flavor: "plain",
      command: controllerCmd,
      cwd: path.join(root, "tui_skeleton"),
      rows: 28,
      cols: 100,
      timeoutMs: args.timeoutMs,
      artifactDir: dir,
      env: {
        BREADBOARD_DEBUG_FAKE_PERMISSION: "0",
        BREADBOARD_DEBUG_FAKE_STREAM: "1",
      },
      actions: async ({ term, waitForPlainIncludes }) => {
        await sleep(1200)

        term.write("\x0b")
        await sleep(250)
        term.write("stream")
        await sleep(700)
        term.write("\r")
        await waitForPlainIncludes("[debug_stream] start", 25_000)

        for (let i = 0; i < 35; i += 1) {
          const cols = i % 2 === 0 ? 90 : 140
          const rows = i % 2 === 0 ? 26 : 44
          term.resize(cols, rows)
          await sleep(55)
        }

        await waitForPlainIncludes("[debug_stream] done", 45_000)

        term.write("streaming resize ok")
        await sleep(100)
        term.write("\r")
        await waitForPlainIncludes("[user]", 45_000)
      },
    })

    const plainDisk = await fs.promises.readFile(capture.plainPath, "utf8").catch(() => "")
    const checks = [
      assert(plainDisk.includes("[debug_stream] done"), "debug stream completes"),
      assert(plainDisk.includes("streaming resize ok"), "UI survives streaming resize storm and can submit"),
    ]
    const pass = checks.every((c) => c.ok)
    await fs.promises.writeFile(path.join(dir, "checks.json"), `${JSON.stringify({ pass, checks }, null, 2)}\n`, "utf8")
    if (!pass) throw new Error("resize storm streaming failed")
  }

  const runScenarioSet = async () => {
    const scenario = args.scenario
    if (scenario === "smoke" || scenario === "all") {
      if (args.smokeFlavor === "plain" || args.smokeFlavor === "both") {
        await runSmoke("plain")
      }
      if (args.smokeFlavor === "tmux_like" || args.smokeFlavor === "both") {
        await runSmoke("tmux_like")
      }
    }
    if (scenario === "permission_variants" || scenario === "all") {
      await runPermissionVariants()
    }
    if (scenario === "palette_commands" || scenario === "all") {
      await runPaletteCommands()
    }
    if (scenario === "file_picker_fuzzy" || scenario === "all") {
      await runFilePickerFuzzy()
    }
    if (scenario === "model_picker_filter" || scenario === "all") {
      await runModelPickerFilter()
    }
    if (scenario === "draft_persistence" || scenario === "all") {
      await runDraftPersistence()
    }
    if (scenario === "slash_help" || scenario === "all") {
      await runSlashHelp()
    }
    if (scenario === "resize_storm" || scenario === "all") {
      await runResizeStorm()
    }
    if (scenario === "resize_storm_streaming" || scenario === "all") {
      await runResizeStormStreaming()
    }
  }

  await runScenarioSet()

  // Goldens are limited to Phase D smoke sanity for now.
  const goldensDir =
    args.goldensDir ||
    path.resolve(root, "..", "docs_tmp", "cli_phase_3", "goldens", "opentui_phaseD_smoke_v1")
  if (args.scenario === "smoke" || args.scenario === "all") {
    await fs.promises.mkdir(goldensDir, { recursive: true })
    const flavors =
      args.smokeFlavor === "both"
        ? (["plain", "tmux_like"] as const)
        : ([args.smokeFlavor] as const)
    for (const flavor of flavors) {
      const caseId = `smoke_${flavor}`
      const plainPath = path.join(artifactsRoot, caseId, "pty_plain.txt")
      const outPath = path.join(goldensDir, `${caseId}.txt`)
      const contents = await fs.promises.readFile(plainPath, "utf8").catch(() => "")
      const stable = contents.replace(/\r/g, "")
      if (args.updateGoldens) {
        await fs.promises.writeFile(outPath, stable, "utf8")
      } else {
        const golden = await fs.promises.readFile(outPath, "utf8").catch(() => "")
        if (!golden) continue
        const needles = [
          "[transcript] saved",
          "[command] set_model",
          "[permission] decision allow-once",
          "file mention test",
        ]
        const checks = needles.map((needle) => assert(stable.includes(needle), `smoke contains ${needle}`))
        const pass = checks.every((c) => c.ok) && needles.every((needle) => golden.includes(needle))
        await fs.promises.writeFile(
          path.join(artifactsRoot, caseId, "golden_compare.json"),
          `${JSON.stringify({ pass, checks, goldenPath: outPath }, null, 2)}\n`,
          "utf8",
        )
        if (!pass) throw new Error(`golden compare failed for ${caseId}`)
      }
    }
  }

  await fs.promises.writeFile(
    path.join(artifactsRoot, "phaseD_summary.json"),
    `${JSON.stringify({ ok: true, artifactsRoot }, null, 2)}\n`,
    "utf8",
  )
}

await main()
