import { spawn, type ChildProcess } from "node:child_process"
import { promises as fs } from "node:fs"
import os from "node:os"
import path from "node:path"
import process from "node:process"
import {
  loadStepsFromFile,
  writeSnapshotFile,
  type SnapshotEntry,
  type Step,
  type KeyName,
} from "./spectator.ts"
import { COMPOSER_READY_MARKERS, includesComposerReady } from "./composerReady"

const DEFAULT_MAX_DURATION_MS = 180_000
const DEFAULT_BOOT_TIMEOUT_MS = 20_000
const DEFAULT_STATE_POLL_MS = 50

interface StateDumpRecord {
  readonly timestamp?: number
  readonly state?: {
    readonly pendingResponse?: boolean
    readonly status?: string
    readonly stats?: { readonly eventCount?: number }
    readonly counts?: { readonly conversation?: number }
    readonly lastConversation?: {
      readonly speaker?: string
      readonly phase?: string
      readonly preview?: string
    } | null
    readonly lastToolEvent?: {
      readonly kind?: string
      readonly status?: string
      readonly text?: string
    } | null
  }
}

export interface EmulatorObserverHarnessOptions {
  readonly steps: Step[]
  readonly command: string
  readonly cwd?: string
  readonly configPath?: string
  readonly baseUrl?: string
  readonly cols: number
  readonly rows: number
  readonly envOverrides?: Record<string, string>
  readonly onInput?: (entry: Record<string, unknown>) => void
  readonly stateDumpPath?: string
  readonly stateDumpMode?: "summary" | "full"
  readonly stateDumpRateMs?: number
  readonly maxDurationMs?: number
  readonly runtimeDir: string
  readonly captureScreenshots?: boolean
  readonly onStepStart?: (entry: {
    readonly stepIndex: number
    readonly step: Step
    readonly paneId: string
    readonly windowId?: string | null
    readonly tabId?: string | null
    readonly x11WindowId?: string | null
  }) => void
}

export interface EmulatorObserverFrame {
  readonly timestamp: number
  readonly label: string
  readonly text: string
  readonly screenshotPath?: string | null
}

export interface EmulatorObserverHarnessResult {
  readonly snapshots: SnapshotEntry[]
  readonly plainBuffer: string
  readonly scrollbackBuffer: string
  readonly frames: EmulatorObserverFrame[]
  readonly metadata: {
    readonly startedAt: number
    readonly finishedAt: number
    readonly durationMs: number
    readonly command: string
    readonly cols: number
    readonly rows: number
    readonly display: string
    readonly paneId: string
    readonly windowId?: string | null
    readonly tabId?: string | null
    readonly x11WindowId?: string | null
    readonly socketPath: string
    readonly runtimeDir: string
  }
}

export class EmulatorObserverHarnessError extends Error {
  readonly result: EmulatorObserverHarnessResult

  constructor(message: string, result: EmulatorObserverHarnessResult, cause?: unknown) {
    super(message)
    this.name = "EmulatorObserverHarnessError"
    this.result = result
    ;(this as { cause?: unknown }).cause = cause
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const shellQuote = (value: string) => `'${value.replace(/'/g, `'\\''`)}'`

const sanitizeLabel = (value: string) => value.replace(/[^A-Za-z0-9_.-]+/g, "_")

const luaQuote = (value: string) => JSON.stringify(value)

const countNonblankLines = (value: string): number =>
  value.split(/\r?\n/).filter((line) => line.trim().length > 0).length

const resolveKey = (key: KeyName): string => {
  switch (key) {
    case "enter":
      return "\r"
    case "escape":
      return "\u001b"
    case "tab":
      return "\t"
    case "space":
      return " "
    case "backspace":
      return "\u007f"
    case "ctrl+backspace":
      return "\u0017"
    case "ctrl+c":
      return "\u0003"
    case "ctrl+d":
      return "\u0004"
    case "ctrl+b":
      return "\u0002"
    case "ctrl+g":
      return "\u0007"
    case "ctrl+z":
      return "\u001a"
    case "ctrl+y":
      return "\u0019"
    case "ctrl+k":
      return "\u000b"
    case "ctrl+l":
      return "\f"
    case "ctrl+p":
      return "\u0010"
    case "ctrl+o":
      return "\u000f"
    case "ctrl+t":
      return "\u0014"
    case "ctrl+v":
      return "\u0016"
    case "ctrl+left":
      return "\u001b[1;5D"
    case "ctrl+right":
      return "\u001b[1;5C"
    case "up":
      return "\u001b[A"
    case "down":
      return "\u001b[B"
    case "left":
      return "\u001b[D"
    case "right":
      return "\u001b[C"
    case "pageup":
      return "\u001b[5~"
    case "pagedown":
      return "\u001b[6~"
    case "home":
      return "\u001b[H"
    case "end":
      return "\u001b[F"
    default:
      throw new Error(`Unsupported key: ${key}`)
  }
}

const buildLaunchScript = async (
  filePath: string,
  options: EmulatorObserverHarnessOptions,
): Promise<void> => {
  const env = {
    ...(options.envOverrides ?? {}),
  }
  if (options.baseUrl) {
    env.BREADBOARD_API_URL = options.baseUrl
  }
  if (options.stateDumpPath) {
    env.BREADBOARD_STATE_DUMP_PATH = options.stateDumpPath
  }
  if (options.stateDumpMode) {
    env.BREADBOARD_STATE_DUMP_MODE = options.stateDumpMode
  }
  if (typeof options.stateDumpRateMs === "number" && Number.isFinite(options.stateDumpRateMs)) {
    env.BREADBOARD_STATE_DUMP_RATE_MS = String(options.stateDumpRateMs)
  }

  const fullCommand = `${options.command}${options.configPath ? ` --config ${shellQuote(options.configPath)}` : ""}`
  const lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    `cd ${shellQuote(options.cwd ?? process.cwd())}`,
    ...Object.entries(env).map(([key, value]) => `export ${key}=${shellQuote(String(value))}`),
    `exec /bin/bash -lc ${shellQuote(fullCommand)}`,
  ]
  await fs.writeFile(filePath, `${lines.join("\n")}\n`, { encoding: "utf8", mode: 0o755 })
}

const spawnWithLogs = (cmd: string, args: string[], env: NodeJS.ProcessEnv, cwd?: string): ChildProcess => {
  return spawn(cmd, args, {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  })
}

const waitForChildExit = async (child: ChildProcess, timeoutMs: number): Promise<void> => {
  await Promise.race([
    new Promise<void>((resolve) => child.once("close", () => resolve())),
    sleep(timeoutMs),
  ])
}

const findProcessIdsByCommandNeedle = async (needle: string): Promise<number[]> => {
  const output = await execCapture("ps", ["-eo", "pid=,args="], process.env, undefined, 3_000).catch(() => "")
  return output
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/^\s*(\d+)\s+(.*)$/)
      if (!match) return null
      const pid = Number(match[1])
      const command = match[2] ?? ""
      if (!Number.isFinite(pid) || pid === process.pid) return null
      if (!command.includes("wezterm-mux-server") || !command.includes(needle)) return null
      return pid
    })
    .filter((pid): pid is number => pid !== null)
}

const killWeztermMuxServersForConfig = async (configPath: string) => {
  const terminate = async (signal: NodeJS.Signals) => {
    const pids = await findProcessIdsByCommandNeedle(configPath)
    for (const pid of pids) {
      try {
        process.kill(pid, signal)
      } catch {
        // The process may have exited between ps and kill.
      }
    }
    return pids.length
  }
  const terminated = await terminate("SIGTERM")
  if (terminated > 0) await sleep(300)
  await terminate("SIGKILL")
}

const startXvfb = async (runtimeDir: string, cols: number, rows: number): Promise<{ child: ChildProcess; display: string }> => {
  const width = Math.max(800, cols * 10)
  const height = Math.max(600, rows * 20)
  const child = spawnWithLogs(
    "Xvfb",
    ["-displayfd", "1", "-screen", "0", `${width}x${height}x24`, "-nolisten", "tcp", "-ac", "-noreset"],
    process.env,
    runtimeDir,
  )
  let stdout = ""
  let stderr = ""
  child.stdout?.on("data", (chunk) => {
    stdout += String(chunk)
  })
  child.stderr?.on("data", (chunk) => {
    stderr += String(chunk)
  })
  const display = await new Promise<string>((resolve, reject) => {
    const started = Date.now()
    const poll = () => {
      const line = stdout.split(/\r?\n/).find((entry) => /^\d+$/.test(entry.trim()))
      if (line) {
        resolve(`:${line.trim()}`)
        return
      }
      if (child.exitCode != null) {
        reject(new Error(`Xvfb exited before reporting display: ${stderr || stdout}`))
        return
      }
      if (Date.now() - started > DEFAULT_BOOT_TIMEOUT_MS) {
        reject(new Error(`Timed out waiting for Xvfb display: ${stderr || stdout}`))
        return
      }
      setTimeout(poll, 50)
    }
    poll()
  })
  return { child, display }
}

const writeWeztermConfig = async (
  configPath: string,
  socketPath: string,
  cols: number,
  rows: number,
  weztermEventsPath: string,
) => {
  const lines = [
    'local wezterm = require "wezterm"',
    `local qc_events_path = ${luaQuote(weztermEventsPath)}`,
    "local function append_qc_event(parts)",
    "  local ok, file = pcall(io.open, qc_events_path, 'a')",
    "  if not ok or not file then",
    "    return",
    "  end",
    "  file:write(table.concat(parts, '\\t') .. '\\n')",
    "  file:close()",
    "end",
    "wezterm.on('user-var-changed', function(window, pane, name, value)",
    "  if name ~= 'BB_RESIZE' then",
    "    return",
    "  end",
    "  local cols_s, rows_s = string.match(value, '^(%d+)x(%d+)$')",
    "  local target_cols = tonumber(cols_s)",
    "  local target_rows = tonumber(rows_s)",
    "  if not target_cols or not target_rows then",
    "    append_qc_event({ 'event=invalid-resize-request', 'value=' .. value })",
    "    return",
    "  end",
    "  local pane_dims = pane:get_dimensions()",
    "  local win_dims = window:get_dimensions()",
    "  local cell_width = math.max(1, math.floor((win_dims.pixel_width or 0) / math.max(1, pane_dims.cols or 1)))",
    "  local cell_height = math.max(1, math.floor((win_dims.pixel_height or 0) / math.max(1, pane_dims.viewport_rows or 1)))",
    "  append_qc_event({ 'event=resize-requested', 'value=' .. value, 'cols=' .. tostring(pane_dims.cols), 'rows=' .. tostring(pane_dims.viewport_rows), 'pixel_width=' .. tostring(win_dims.pixel_width), 'pixel_height=' .. tostring(win_dims.pixel_height) })",
    "  window:set_inner_size(target_cols * cell_width, target_rows * cell_height)",
    "end)",
    "wezterm.on('window-resized', function(window, pane)",
    "  local pane_dims = pane:get_dimensions()",
    "  local win_dims = window:get_dimensions()",
    "  append_qc_event({ 'event=window-resized', 'cols=' .. tostring(pane_dims.cols), 'rows=' .. tostring(pane_dims.viewport_rows), 'pixel_width=' .. tostring(win_dims.pixel_width), 'pixel_height=' .. tostring(win_dims.pixel_height) })",
    "end)",
    "return {",
    "  enable_wayland = false,",
    "  audible_bell = 'Disabled',",
    "  check_for_updates = false,",
    "  automatically_reload_config = false,",
    "  adjust_window_size_when_changing_font_size = false,",
    `  initial_cols = ${cols},`,
    `  initial_rows = ${rows},`,
    "  window_padding = { left = 0, right = 0, top = 0, bottom = 0 },",
    "  window_close_confirmation = 'NeverPrompt',",
    `  unix_domains = { { name = 'localsock', socket_path = ${luaQuote(socketPath)} } },`,
    "}",
  ]
  await fs.writeFile(configPath, `${lines.join("\n")}\n`, "utf8")
}

const execCapture = async (
  cmd: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  cwd?: string,
  timeoutMs = 10_000,
): Promise<string> => {
  return await new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"] })
    let stdout = ""
    let stderr = ""
    const timer = setTimeout(() => {
      child.kill("SIGTERM")
      reject(new Error(`Command timed out: ${cmd} ${args.join(" ")}`))
    }, timeoutMs)
    child.stdout?.on("data", (chunk) => (stdout += String(chunk)))
    child.stderr?.on("data", (chunk) => (stderr += String(chunk)))
    child.on("error", (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.on("close", (code) => {
      clearTimeout(timer)
      if (code === 0) {
        resolve(stdout)
      } else {
        reject(new Error(`${cmd} exited with code ${code}: ${stderr.trim() || stdout.trim()}`))
      }
    })
  })
}

const buildCliEnv = (display: string, runtimeDir: string, socketPath: string): NodeJS.ProcessEnv => ({
  ...process.env,
  DISPLAY: display,
  XDG_RUNTIME_DIR: runtimeDir,
  WEZTERM_UNIX_SOCKET: socketPath,
})

const waitForClient = async (env: NodeJS.ProcessEnv, gui: ChildProcess, guiLogPath: string): Promise<void> => {
  const started = Date.now()
  while (Date.now() - started < DEFAULT_BOOT_TIMEOUT_MS) {
    if (gui.exitCode != null) {
      let guiLog = ""
      try { guiLog = await fs.readFile(guiLogPath, "utf8") } catch {}
      throw new Error(`WezTerm GUI exited early with code ${gui.exitCode}: ${guiLog.trim() || "(no gui log)"}`)
    }
    try {
      const output = await execCapture("wezterm", ["cli", "--no-auto-start", "--prefer-mux", "list-clients", "--format", "json"], env, undefined, 3_000)
      const parsed = JSON.parse(output) as unknown[]
      if (Array.isArray(parsed) && parsed.length > 0) return
    } catch {
      // keep polling
    }
    await sleep(100)
  }
  throw new Error("Timed out waiting for WezTerm GUI client connection")
}

const readLatestStateDumpRecord = async (target: string | undefined): Promise<StateDumpRecord | null> => {
  if (!target) return null
  try {
    const raw = await fs.readFile(target, "utf8")
    const lines = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
    if (lines.length === 0) return null
    return JSON.parse(lines[lines.length - 1]!) as StateDumpRecord
  } catch {
    return null
  }
}

const parsePaneId = (value: string): string => {
  const trimmed = value.trim()
  if (!/^\d+$/.test(trimmed)) {
    throw new Error(`Unexpected pane id: ${value}`)
  }
  return trimmed
}

interface WeztermResizeEvent {
  event?: string
  value?: string
  cols?: number
  rows?: number
  pixel_width?: number
  pixel_height?: number
}

interface WeztermPaneInfo {
  readonly window_id: number
  readonly pane_id: number
  readonly tab_id?: number
  readonly size?: {
    readonly rows?: number
    readonly cols?: number
    readonly pixel_width?: number
    readonly pixel_height?: number
  }
}

interface X11WindowGeometry {
  readonly width: number
  readonly height: number
}

const parseWeztermEventLine = (line: string): WeztermResizeEvent => {
  const event: WeztermResizeEvent = {}
  for (const field of line.split("\t")) {
    const [key, ...rest] = field.split("=")
    const value = rest.join("=")
    if (key === "event") event.event = value
    else if (key === "value") event.value = value
    else if (key === "cols") event.cols = Number(value)
    else if (key === "rows") event.rows = Number(value)
    else if (key === "pixel_width") event.pixel_width = Number(value)
    else if (key === "pixel_height") event.pixel_height = Number(value)
  }
  return event
}

const readWeztermEvents = async (filePath: string): Promise<WeztermResizeEvent[]> => {
  try {
    const raw = await fs.readFile(filePath, "utf8")
    return raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map(parseWeztermEventLine)
  } catch {
    return []
  }
}

const listWeztermPanes = async (env: NodeJS.ProcessEnv): Promise<WeztermPaneInfo[]> => {
  const output = await execCapture(
    "wezterm",
    ["cli", "--no-auto-start", "--prefer-mux", "list", "--format", "json"],
    env,
    undefined,
    5_000,
  )
  return JSON.parse(output) as WeztermPaneInfo[]
}

const resolveWeztermX11WindowId = async (display: string): Promise<string> => {
  const output = await execCapture("xwininfo", ["-display", display, "-root", "-tree"], process.env, undefined, 5_000)
  const lines = output.split(/\r?\n/)
  for (const line of lines) {
    if (line.includes("org.wezfurlong.wezterm")) {
      const match = /\b(0x[0-9a-fA-F]+)\b/.exec(line)
      if (match) return match[1]
    }
  }
  throw new Error("Could not resolve WezTerm X11 window id from xwininfo")
}

const readX11WindowGeometry = async (display: string, x11WindowId: string): Promise<X11WindowGeometry> => {
  const output = await execCapture("xwininfo", ["-display", display, "-id", x11WindowId], process.env, undefined, 5_000)
  const widthMatch = /^\s*Width:\s+(\d+)\s*$/m.exec(output)
  const heightMatch = /^\s*Height:\s+(\d+)\s*$/m.exec(output)
  if (!widthMatch || !heightMatch) {
    throw new Error(`Could not parse X11 geometry for window ${x11WindowId}`)
  }
  return {
    width: Number(widthMatch[1]),
    height: Number(heightMatch[1]),
  }
}

export const runWeztermObserverHarness = async (
  options: EmulatorObserverHarnessOptions,
): Promise<EmulatorObserverHarnessResult> => {
  const startedAt = Date.now()
  const snapshots: SnapshotEntry[] = []
  const frames: EmulatorObserverFrame[] = []
  let plainBuffer = ""
  const runtimeDir = options.runtimeDir
  const observerRuntimeDir = path.join(runtimeDir, "observer_runtime")
  const xdgRuntimeDir = await fs.mkdtemp(path.join(os.tmpdir(), "bb-wezterm-xdg-"))
  const observerTextDir = path.join(runtimeDir, "observer_text")
  const observerPngDir = path.join(runtimeDir, "observer_png")
  await fs.mkdir(observerRuntimeDir, { recursive: true })
  await fs.mkdir(observerTextDir, { recursive: true })
  await fs.mkdir(observerPngDir, { recursive: true })
  const socketPath = path.join(xdgRuntimeDir, "wezterm.sock")
  const configPath = path.join(observerRuntimeDir, "wezterm.lua")
  const launchScriptPath = path.join(observerRuntimeDir, "launch.sh")
  const guiLogPath = path.join(observerRuntimeDir, "wezterm-gui.log")
  const muxLogPath = path.join(observerRuntimeDir, "wezterm-mux.log")
  const weztermEventsPath = path.join(observerRuntimeDir, "wezterm-events.ndjson")
  await fs.writeFile(weztermEventsPath, "", "utf8")
  await writeWeztermConfig(configPath, socketPath, options.cols, options.rows, weztermEventsPath)
  await buildLaunchScript(launchScriptPath, options)

  const logInput = (entry: Record<string, unknown>) => {
    options.onInput?.({ timestamp: Date.now(), ...entry })
  }

  const { child: xvfb, display } = await startXvfb(runtimeDir, options.cols, options.rows)
  const baseEnv = buildCliEnv(display, xdgRuntimeDir, socketPath)
  const muxLog = await fs.open(muxLogPath, "a")
  const guiLog = await fs.open(guiLogPath, "a")
  const mux = spawn("wezterm-mux-server", ["--config-file", configPath, "--daemonize"], {
    cwd: observerRuntimeDir,
    env: baseEnv,
    stdio: ["ignore", muxLog.fd, muxLog.fd],
  })
  await waitForChildExit(mux, 250)
  const gui = spawn("wezterm", ["--config-file", configPath, "connect", "localsock"], {
    cwd: observerRuntimeDir,
    env: baseEnv,
    stdio: ["ignore", guiLog.fd, guiLog.fd],
  })

  let paneId = ""
  let x11WindowId = ""
  let paneWindowId: string | null = null
  let paneTabId: string | null = null
  let thrown: unknown = null
  let scrollbackBuffer = ""

  const cleanup = async () => {
    try {
      if (paneId) {
        await execCapture("wezterm", ["cli", "--no-auto-start", "--prefer-mux", "kill-pane", "--pane-id", paneId], baseEnv, undefined, 2_000).catch(() => "")
      }
    } catch {
      // ignore
    }
    for (const child of [gui, xvfb]) {
      if (child.exitCode == null) {
        try {
          child.kill("SIGTERM")
        } catch {
          // ignore
        }
      }
    }
    await sleep(200)
    for (const child of [gui, xvfb]) {
      if (child.exitCode == null) {
        try {
          child.kill("SIGKILL")
        } catch {
          // ignore
        }
      }
    }
    await killWeztermMuxServersForConfig(configPath).catch(() => undefined)
    await guiLog.close().catch(() => undefined)
    await muxLog.close().catch(() => undefined)
  }

  const readPaneText = async (startLine = 0): Promise<string> => {
    if (!paneId) return ""
    const output = await execCapture(
      "wezterm",
      ["cli", "--no-auto-start", "--prefer-mux", "get-text", "--pane-id", paneId, "--start-line", String(startLine)],
      baseEnv,
      undefined,
      5_000,
    )
    if (startLine == 0) {
      plainBuffer = output
    }
    return output
  }

  const readPaneTextForSnapshot = async (): Promise<string> => {
    const first = await readPaneText()
    if (countNonblankLines(first) >= 3) return first

    const started = Date.now()
    let latest = first
    while (Date.now() - started < 1_500) {
      await sleep(75)
      latest = await readPaneText().catch(() => latest)
      if (countNonblankLines(latest) >= 3) return latest
    }
    return latest
  }

  const takeSnapshot = async (label: string) => {
    const text = await readPaneTextForSnapshot()
    const safe = sanitizeLabel(label)
    const textPath = path.join(observerTextDir, `${safe}.txt`)
    await fs.writeFile(textPath, text, "utf8")
    let screenshotPath: string | null = null
    if (options.captureScreenshots !== false) {
      screenshotPath = path.join(observerPngDir, `${safe}.png`)
      const captureTarget = x11WindowId || "root"
      await execCapture("import", ["-display", display, "-window", captureTarget, screenshotPath], baseEnv, undefined, 10_000).catch(() => {
        screenshotPath = null
        return ""
      })
    }
    frames.push({ timestamp: Date.now(), label, text, screenshotPath })
    snapshots.push({ label, cleaned: text })
  }

  const waitForOutput = async (text: string, timeoutMs = 10_000) => {
    const begin = Date.now()
    while (Date.now() - begin < timeoutMs) {
      const current = await readPaneText().catch(() => "")
      if (current.includes(text)) return
      await sleep(100)
    }
    throw new Error(`Timed out waiting for output containing ${JSON.stringify(text)}`)
  }

  const waitForComposerReady = async (timeoutMs = 10_000) => {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      const current = await readPaneText().catch(() => plainBuffer)
      if (includesComposerReady(current)) return
      await sleep(DEFAULT_STATE_POLL_MS)
    }
    throw new Error(`Timed out waiting for composer readiness via markers: ${COMPOSER_READY_MARKERS.join(", ")}`)
  }

  const waitForState = async (criteria: Extract<Step, { readonly action: "waitForState" }>) => {
    const timeoutMs = criteria.timeoutMs ?? 10_000
    const start = Date.now()
    const baseline = await readLatestStateDumpRecord(options.stateDumpPath)
    const baselineTimestamp = baseline?.timestamp ?? 0
    const matches = (record: StateDumpRecord | null): boolean => {
      if (!record?.state) return false
      if (criteria.fresh && (record.timestamp ?? 0) <= baselineTimestamp) return false
      if (criteria.pendingResponse != null && record.state.pendingResponse !== criteria.pendingResponse) return false
      if (criteria.statusIncludes && !(record.state.status ?? "").includes(criteria.statusIncludes)) return false
      if (criteria.conversationCountAtLeast != null && (record.state.counts?.conversation ?? 0) < criteria.conversationCountAtLeast) return false
      if (criteria.eventCountAtLeast != null && (record.state.stats?.eventCount ?? 0) < criteria.eventCountAtLeast) return false
      if (criteria.lastConversationSpeaker && (record.state.lastConversation?.speaker ?? "") !== criteria.lastConversationSpeaker) return false
      if (criteria.lastConversationPhase && (record.state.lastConversation?.phase ?? "") !== criteria.lastConversationPhase) return false
      if (criteria.lastConversationPreviewIncludes && !(record.state.lastConversation?.preview ?? "").includes(criteria.lastConversationPreviewIncludes)) return false
      if (criteria.lastToolEventKind && (record.state.lastToolEvent?.kind ?? "") !== criteria.lastToolEventKind) return false
      if (criteria.lastToolEventStatus && (record.state.lastToolEvent?.status ?? "") !== criteria.lastToolEventStatus) return false
      if (criteria.lastToolEventTextIncludes && !(record.state.lastToolEvent?.text ?? "").includes(criteria.lastToolEventTextIncludes)) return false
      return true
    }
    while (Date.now() - start < timeoutMs) {
      const latest = await readLatestStateDumpRecord(options.stateDumpPath)
      if (matches(latest)) return
      await sleep(DEFAULT_STATE_POLL_MS)
    }
    throw new Error(`Timed out waiting for matching state criteria after ${timeoutMs}ms`)
  }

  const sendText = async (text: string, noPaste = true) => {
    if (!paneId) throw new Error("Pane not ready")
    const args = ["cli", "--no-auto-start", "--prefer-mux", "send-text", "--pane-id", paneId]
    if (noPaste) args.push("--no-paste")
    args.push(text)
    await execCapture("wezterm", args, baseEnv, undefined, 5_000)
  }

  const getPaneInfo = async () => {
    const panes = await listWeztermPanes(baseEnv)
    const pane = panes.find((entry) => String(entry.pane_id) === paneId)
    if (!pane?.size) {
      throw new Error(`Could not find WezTerm pane ${paneId} in cli list output`)
    }
    return pane
  }

  const resizePaneWindow = async (cols: number, rows: number, timeoutMs: number) => {
    const current = await getPaneInfo()
    const currentCols = current.size?.cols ?? 0
    const currentRows = current.size?.rows ?? 0
    const currentPixelWidth = current.size?.pixel_width ?? 0
    const currentPixelHeight = current.size?.pixel_height ?? 0
    if (!x11WindowId || currentCols <= 0 || currentRows <= 0 || currentPixelWidth <= 0 || currentPixelHeight <= 0) {
      throw new Error(`Incomplete WezTerm pane geometry for resize: ${JSON.stringify(current)}`)
    }
    const x11Geometry = await readX11WindowGeometry(display, x11WindowId)
    const widthDecoration = Math.max(0, x11Geometry.width - currentPixelWidth)
    const heightDecoration = Math.max(0, x11Geometry.height - currentPixelHeight)
    const targetInnerWidth = Math.max(100, Math.round((currentPixelWidth / currentCols) * cols))
    const targetInnerHeight = Math.max(100, Math.round((currentPixelHeight / currentRows) * rows))
    const targetPixelWidth = targetInnerWidth + widthDecoration
    const targetPixelHeight = targetInnerHeight + heightDecoration
    await execCapture(
      "python",
      [
        path.join(path.dirname(new URL(import.meta.url).pathname), "x11ResizeWindow.py"),
        display,
        x11WindowId,
        String(targetPixelWidth),
        String(targetPixelHeight),
      ],
      process.env,
      undefined,
      5_000,
    )
    const started = Date.now()
    while (Date.now() - started < timeoutMs) {
      const latest = await getPaneInfo()
      if (latest.size?.cols === cols && latest.size?.rows === rows) return
      await sleep(100)
    }
    throw new Error(`Timed out waiting for WezTerm resize to ${cols}x${rows}`)
  }

  try {
    await waitForClient(baseEnv, gui, guiLogPath)
    x11WindowId = await resolveWeztermX11WindowId(display)
    const initialPanes = await listWeztermPanes(baseEnv)
    const initialWindowId = initialPanes[0]?.window_id
    if (typeof initialWindowId !== "number") {
      throw new Error("Could not resolve initial WezTerm window_id for same-window spawn")
    }
    paneId = parsePaneId(
      await execCapture(
        "wezterm",
        [
          "cli",
          "--no-auto-start",
          "--prefer-mux",
          "spawn",
          "--window-id",
          String(initialWindowId),
          "--cwd",
          options.cwd ?? process.cwd(),
          "--",
          "/bin/bash",
          launchScriptPath,
        ],
        baseEnv,
        undefined,
        10_000,
      ),
    )
    const initialPaneInfo = await getPaneInfo()
    paneWindowId = typeof initialPaneInfo.window_id === "number" ? String(initialPaneInfo.window_id) : null
    paneTabId = typeof initialPaneInfo.tab_id === "number" ? String(initialPaneInfo.tab_id) : null
    if (paneTabId) {
      await execCapture(
        "wezterm",
        ["cli", "--no-auto-start", "--prefer-mux", "activate-tab", "--tab-id", paneTabId],
        baseEnv,
        undefined,
        5_000,
      ).catch(() => "")
    }
    await execCapture(
      "wezterm",
      ["cli", "--no-auto-start", "--prefer-mux", "activate-pane", "--pane-id", paneId],
      baseEnv,
      undefined,
      5_000,
    ).catch(() => "")
    await sleep(150)

    for (const [stepIndex, step] of options.steps.entries()) {
      if (options.maxDurationMs && Date.now() - startedAt > options.maxDurationMs) {
        throw new Error(`Harness timeout: exceeded ${options.maxDurationMs}ms total runtime`)
      }
      options.onStepStart?.({
        stepIndex,
        step,
        paneId,
        windowId: paneWindowId,
        tabId: paneTabId,
        x11WindowId: x11WindowId || null,
      })
      switch (step.action) {
        case "wait":
          await sleep(step.ms)
          break
        case "type":
          if (step.typingDelayMs && step.typingDelayMs > 0) {
            for (const char of step.text) {
              await sendText(char)
              logInput({ action: "type", char })
              await sleep(step.typingDelayMs)
            }
          } else {
            await sendText(step.text)
            logInput({ action: "type", text: step.text })
          }
          break
        case "paste":
          await sendText(step.text, false)
          logInput({ action: "paste", length: step.text.length })
          break
        case "press": {
          const repeat = step.repeat ?? 1
          for (let i = 0; i < repeat; i += 1) {
            await sendText(resolveKey(step.key))
            logInput({ action: "press", key: step.key })
            if (step.delayMs && step.delayMs > 0) await sleep(step.delayMs)
          }
          break
        }
        case "snapshot":
          await takeSnapshot(step.label)
          break
        case "snapshotBurst": {
          const count = Math.max(1, step.count)
          const intervalMs = Math.max(0, step.intervalMs ?? 100)
          for (let i = 0; i < count; i += 1) {
            await takeSnapshot(`${step.label}-${i}`)
            if (i + 1 < count && intervalMs > 0) {
              await sleep(intervalMs)
            }
          }
          break
        }
        case "waitFor":
          await waitForOutput(step.text, step.timeoutMs)
          break
        case "waitForComposerReady":
          await waitForComposerReady(step.timeoutMs)
          break
        case "waitForState":
          await waitForState(step)
          break
        case "log":
          console.log(`[wezterm-observer] ${step.message}`)
          break
        case "resize":
          logInput({ action: "resize", cols: step.cols, rows: step.rows })
          await resizePaneWindow(step.cols, step.rows, Math.max(4_000, step.delayMs ?? 0))
          if (step.delayMs && step.delayMs > 0) await sleep(step.delayMs)
          break
      }
    }
  } catch (error) {
    thrown = error
  }

  plainBuffer = await readPaneText(0).catch(() => plainBuffer)
  scrollbackBuffer = await readPaneText(-4000).catch(() => plainBuffer)

  await cleanup()
  await fs.rm(xdgRuntimeDir, { recursive: true, force: true }).catch(() => undefined)
  const finishedAt = Date.now()
  const result: EmulatorObserverHarnessResult = {
    snapshots,
    plainBuffer,
    scrollbackBuffer,
    frames,
    metadata: {
      startedAt,
      finishedAt,
      durationMs: finishedAt - startedAt,
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      display,
      paneId,
      windowId: paneWindowId,
      tabId: paneTabId,
      x11WindowId: x11WindowId || null,
      socketPath,
      runtimeDir,
    },
  }
  if (thrown) {
    throw new EmulatorObserverHarnessError((thrown as Error).message, result, thrown)
  }
  return result
}

const parseArgs = () => {
  const args = process.argv.slice(2)
  let scriptPath: string | undefined
  let command = "bb repl"
  let cwd: string | undefined
  let configPath: string | undefined
  let baseUrl: string | undefined
  let runtimeDir: string | undefined
  let snapshotsPath: string | undefined
  let metadataPath: string | undefined
  let framesPath: string | undefined
  let inputLogPath: string | undefined
  let stateDumpPath: string | undefined
  let cols = 120
  let rows = 36
  for (let i = 0; i < args.length; i += 1) {
    switch (args[i]) {
      case "--script": scriptPath = args[++i]; break
      case "--cmd": command = args[++i]; break
      case "--cwd": cwd = args[++i]; break
      case "--config": configPath = args[++i]; break
      case "--base-url": baseUrl = args[++i]; break
      case "--runtime-dir": runtimeDir = args[++i]; break
      case "--snapshots": snapshotsPath = args[++i]; break
      case "--metadata": metadataPath = args[++i]; break
      case "--frames": framesPath = args[++i]; break
      case "--input-log": inputLogPath = args[++i]; break
      case "--state-dump": stateDumpPath = args[++i]; break
      case "--cols": cols = Number(args[++i]); break
      case "--rows": rows = Number(args[++i]); break
      default: break
    }
  }
  if (!scriptPath || !runtimeDir || !snapshotsPath || !metadataPath || !framesPath || !inputLogPath || !stateDumpPath) {
    throw new Error("missing required emulator harness arguments")
  }
  return { scriptPath, command, cwd, configPath, baseUrl, runtimeDir, snapshotsPath, metadataPath, framesPath, inputLogPath, stateDumpPath, cols, rows }
}

const main = async () => {
  const args = parseArgs()
  const steps = await loadStepsFromFile(args.scriptPath)
  const inputLog: string[] = []
  let result: EmulatorObserverHarnessResult
  try {
    result = await runWeztermObserverHarness({
      steps,
      command: args.command,
      cwd: args.cwd,
      configPath: args.configPath,
      baseUrl: args.baseUrl,
      cols: args.cols,
      rows: args.rows,
      runtimeDir: args.runtimeDir,
      stateDumpPath: args.stateDumpPath,
      stateDumpMode: "summary",
      stateDumpRateMs: 100,
      onInput: (entry) => inputLog.push(JSON.stringify(entry)),
      captureScreenshots: true,
      maxDurationMs: DEFAULT_MAX_DURATION_MS,
    })
  } catch (error) {
    if (error instanceof EmulatorObserverHarnessError) {
      result = error.result
    } else {
      throw error
    }
  }
  await writeSnapshotFile(result.snapshots, args.snapshotsPath)
  await fs.writeFile(args.metadataPath, `${JSON.stringify(result.metadata, null, 2)}\n`, "utf8")
  await fs.writeFile(args.framesPath, `${result.frames.map((frame) => JSON.stringify(frame)).join("\n")}\n`, "utf8")
  await fs.writeFile(args.inputLogPath, inputLog.length > 0 ? `${inputLog.join("\n")}\n` : "", "utf8")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void main().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
