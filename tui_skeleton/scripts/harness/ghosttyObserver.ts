import { spawn, type ChildProcess } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"
import { type SnapshotEntry, type Step } from "./spectator.ts"
import { includesComposerReady } from "./composerReady.ts"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const DEFAULT_BOOT_TIMEOUT_MS = 20_000
const DEFAULT_GNOME_SHELL_BOOT_MS = 7_000
const X11_CONTROL_SCRIPT = path.resolve(__dirname, "x11_window_control.py")
const NATIVE_EXPORT_FLAG = "BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT"

export interface GhosttyObserverHarnessOptions {
  readonly steps: Step[]
  readonly command: string
  readonly cwd?: string
  readonly configPath?: string
  readonly baseUrl?: string
  readonly cols: number
  readonly rows: number
  readonly runtimeDir: string
  readonly stateDumpPath?: string
  readonly stateDumpMode?: "summary" | "full"
  readonly stateDumpRateMs?: number
  readonly envOverrides?: Record<string, string>
  readonly captureScreenshots?: boolean
  readonly onStepStart?: (entry: {
    readonly stepIndex: number
    readonly step: Step
    readonly display: string
    readonly x11WindowId?: string | null
  }) => void
}

export interface GhosttyObserverFrame {
  readonly timestamp: number
  readonly label: string
  readonly text: string
  readonly screenshotPath?: string | null
}

export interface GhosttyObserverHarnessResult {
  readonly snapshots: SnapshotEntry[]
  readonly plainBuffer: string
  readonly scrollbackBuffer: string
  readonly frames: GhosttyObserverFrame[]
  readonly metadata: {
    readonly startedAt: number
    readonly finishedAt: number
    readonly durationMs: number
    readonly command: string
    readonly cols: number
    readonly rows: number
    readonly display: string
    readonly runtimeDir: string
    readonly x11WindowId?: string | null
    readonly launchStatus: "success" | "partial_success" | "failed"
    readonly x11CandidateWindowCount: number
  }
}

export class GhosttyObserverHarnessError extends Error {
  readonly result: GhosttyObserverHarnessResult

  constructor(message: string, result: GhosttyObserverHarnessResult, cause?: unknown) {
    super(message)
    this.name = "GhosttyObserverHarnessError"
    this.result = result
    ;(this as { cause?: unknown }).cause = cause
  }
}

interface VisibleX11Window {
  readonly id: string
  readonly width: number
  readonly height: number
  readonly x: number
  readonly y: number
  readonly label: string
  readonly isGhostty: boolean
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const shellQuote = (value: string) => `'${value.replace(/'/g, `'\\''`)}'`

const sanitizeLabel = (value: string) => value.replace(/[^A-Za-z0-9_.-]+/g, "_")

const execCapture = async (
  cmd: string,
  args: string[],
  env: NodeJS.ProcessEnv,
  cwd?: string,
): Promise<{ stdout: string; stderr: string; exitCode: number }> => {
  const child = spawn(cmd, args, {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  })
  let stdout = ""
  let stderr = ""
  child.stdout?.on("data", (chunk) => {
    stdout += String(chunk)
  })
  child.stderr?.on("data", (chunk) => {
    stderr += String(chunk)
  })
  const exitCode = await new Promise<number>((resolve, reject) => {
    child.once("error", reject)
    child.once("close", (code) => resolve(code ?? 0))
  })
  return { stdout, stderr, exitCode }
}

const spawnWithLogs = (cmd: string, args: string[], env: NodeJS.ProcessEnv, cwd?: string): ChildProcess => {
  return spawn(cmd, args, {
    cwd,
    env,
    stdio: ["ignore", "pipe", "pipe"],
  })
}

const computePixelSize = (cols: number, rows: number) => ({
  width: Math.max(1024, cols * 12),
  height: Math.max(768, rows * 24),
})

const startXvfb = async (runtimeDir: string, cols: number, rows: number): Promise<{ child: ChildProcess; display: string }> => {
  const { width, height } = computePixelSize(cols, rows)
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

const extractVisibleWindows = (treeText: string): VisibleX11Window[] => {
  const parseX11Coordinate = (value: string): number => {
    if (value.startsWith("+-")) {
      return -Number(value.slice(2))
    }
    return Number(value)
  }

  return treeText
    .split(/\r?\n/)
    .map((line) => {
      const match = line.match(/^\s+(0x[0-9a-f]+)\b.*?\s(\d+)x(\d+)((?:\+-|[+-])\d+)([+-]\d+)/i)
      if (!match) {
        return null
      }
      const width = Number(match[2])
      const height = Number(match[3])
      const x = parseX11Coordinate(match[4])
      const y = parseX11Coordinate(match[5])
      const lowered = line.toLowerCase()
      const isGhostty = lowered.includes("ghostty") || lowered.includes("com.mitchellh.ghostty")
      const isShellInfrastructure =
        lowered.includes("mutter guard window") ||
        lowered.includes("gnome-shell") ||
        lowered.includes("gnome shell") ||
        lowered.includes("xdg-desktop-portal") ||
        lowered.includes("ibus-")
      const coordinateFloor = isGhostty ? -120 : -10
      if (width >= 40 && height >= 10 && x >= coordinateFloor && y >= coordinateFloor) {
        if (!isGhostty && isShellInfrastructure) {
          return null
        }
        return {
          id: match[1],
          width,
          height,
          x,
          y,
          label: line.trim(),
          isGhostty,
        }
      }
      return null
    })
    .filter((value): value is VisibleX11Window => Boolean(value))
}

const choosePrimaryWindow = (windows: readonly VisibleX11Window[]): VisibleX11Window | null => {
  return [...windows].sort((left, right) => {
    if (left.isGhostty !== right.isGhostty) {
      return left.isGhostty ? -1 : 1
    }
    return right.width * right.height - left.width * left.height
  })[0] ?? null
}

const discoverGhosttyWindow = async (display: string): Promise<{ tree: string; windows: VisibleX11Window[]; chosen: VisibleX11Window | null }> => {
  const tree = await execCapture("xwininfo", ["-root", "-tree", "-display", display], process.env)
  const windows = extractVisibleWindows(tree.stdout)
  const chosen = choosePrimaryWindow(windows)
  return { tree: tree.stdout, windows, chosen }
}

const buildLaunchScript = async (
  filePath: string,
  options: GhosttyObserverHarnessOptions,
  launchCommand: string,
  runtimeArtifactsDir: string,
  display: string,
): Promise<void> => {
  const nativeScrollbackExportEnabled = options.envOverrides?.[NATIVE_EXPORT_FLAG] === "1"
  const nativeExportDir = path.join(runtimeArtifactsDir, "ghostty-native-export")
  const fakeBinDir = path.join(runtimeArtifactsDir, "fakebin")
  const ghosttyConfigHome = path.join(runtimeArtifactsDir, "ghostty-config-home")
  const ghosttyStdoutPath = path.join(runtimeArtifactsDir, "ghostty.stdout.log")
  const ghosttyStderrPath = path.join(runtimeArtifactsDir, "ghostty.stderr.log")
  const gnomeShellStdoutPath = path.join(runtimeArtifactsDir, "gnome-shell.stdout.log")
  const gnomeShellStderrPath = path.join(runtimeArtifactsDir, "gnome-shell.stderr.log")
  if (nativeScrollbackExportEnabled) {
    await fs.mkdir(nativeExportDir, { recursive: true })
    await fs.mkdir(fakeBinDir, { recursive: true })
    const ghosttyConfigDir = path.join(ghosttyConfigHome, "ghostty")
    await fs.mkdir(ghosttyConfigDir, { recursive: true })
    await fs.writeFile(
      path.join(ghosttyConfigDir, "config"),
      [
        "# BreadBoard QC native-export observer config.",
        "# Keep this isolated from the user's Ghostty config so native claim gates are reproducible.",
        "keybind = ctrl+shift+o=write_scrollback_file:open",
        "keybind = ctrl+shift+t=write_screen_file:open",
        "",
      ].join("\n"),
      "utf8",
    )
    const xdgOpenShimPath = path.join(fakeBinDir, "xdg-open")
    const xdgOpenShim = [
      "#!/usr/bin/env bash",
      "set -euo pipefail",
      'src="${1:-}"',
      'dest_dir="${BREADBOARD_GHOSTTY_EXPORT_DIR:?}"',
      'mkdir -p "$dest_dir"',
      'dest="$dest_dir/export_$(date +%s%N).txt"',
      'if [[ -n "$src" && -f "$src" ]]; then cp "$src" "$dest"; else : > "$dest"; fi',
      'printf "%s\t%s\n" "$src" "$dest" >> "$dest_dir/open_log.tsv"',
      "exit 0",
    ]
    await fs.writeFile(xdgOpenShimPath, `${xdgOpenShim.join("\n")}\n`, { encoding: "utf8", mode: 0o755 })
  }
  const env: Record<string, string | undefined> = {
    ...(options.envOverrides ?? {}),
    DISPLAY: display,
    XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR ?? "/run/user/1041",
    GDK_BACKEND: "x11",
    LIBGL_ALWAYS_SOFTWARE: "1",
    NO_AT_BRIDGE: "1",
    ...(nativeScrollbackExportEnabled
      ? {
          BREADBOARD_GHOSTTY_EXPORT_DIR: nativeExportDir,
          XDG_CONFIG_HOME: ghosttyConfigHome,
          PATH: `${fakeBinDir}:${process.env.PATH ?? ""}`,
        }
      : {}),
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
  const commandScriptPath = path.join(runtimeArtifactsDir, "ghostty-command.sh")
  const commandLines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    `cd ${shellQuote(options.cwd ?? process.cwd())}`,
    `exec /bin/bash -lc ${shellQuote(launchCommand)}`,
  ]
  await fs.writeFile(commandScriptPath, `${commandLines.join("\n")}\n`, { encoding: "utf8", mode: 0o755 })
  const ghosttyArgs = [
    "--gtk-single-instance=false",
    `--window-width=${options.cols}`,
    `--window-height=${options.rows}`,
    "--window-save-state=never",
    "-e",
    "bash",
    shellQuote(commandScriptPath),
  ]
  const lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    ...Object.entries(env).map(([key, value]) => `export ${key}=${shellQuote(String(value))}`),
    `cd ${shellQuote(options.cwd ?? process.cwd())}`,
    `gnome-shell --x11 --sm-disable >${shellQuote(gnomeShellStdoutPath)} 2>${shellQuote(gnomeShellStderrPath)} &`,
    "GS_PID=$!",
    `sleep ${Math.ceil(DEFAULT_GNOME_SHELL_BOOT_MS / 1000)}`,
    `ghostty ${ghosttyArgs.join(" ")} >${shellQuote(ghosttyStdoutPath)} 2>${shellQuote(ghosttyStderrPath)} &`,
    "GHOSTTY_PID=$!",
    `echo "$GS_PID" > ${shellQuote(path.join(runtimeArtifactsDir, "gnome-shell.pid"))}`,
    `echo "$GHOSTTY_PID" > ${shellQuote(path.join(runtimeArtifactsDir, "ghostty.pid"))}`,
    "wait $GHOSTTY_PID || true",
    "kill $GS_PID >/dev/null 2>&1 || true",
  ]
  await fs.writeFile(filePath, `${lines.join("\n")}\n`, { encoding: "utf8", mode: 0o755 })
}

const readNativeExportLog = async (exportDir: string): Promise<Array<{ sourcePath: string; copiedPath: string }>> => {
  const logPath = path.join(exportDir, "open_log.tsv")
  const text = await fs.readFile(logPath, "utf8").catch(() => "")
  return text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => {
      const [sourcePath = "", copiedPath = ""] = line.split("\t")
      return { sourcePath, copiedPath }
    })
    .filter((entry) => entry.copiedPath.length > 0)
}

const waitForNativeExport = async (
  exportDir: string,
  previousCount: number,
  timeoutMs = 4_000,
): Promise<{ sourcePath: string; copiedPath: string } | null> => {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    const entries = await readNativeExportLog(exportDir)
    const next = entries[previousCount]
    if (next) return next
    await sleep(100)
  }
  return null
}

const captureNativeGhosttyFile = async (args: {
  readonly display: string
  readonly windowId: string
  readonly exportDir: string
  readonly key: "ctrl+shift+t" | "ctrl+shift+o"
  readonly outputPath: string
}): Promise<string | null> => {
  const before = await readNativeExportLog(args.exportDir)
  await runX11Control(args.display, args.windowId, "click")
  await runX11Control(args.display, args.windowId, "focus")
  await runX11Control(args.display, args.windowId, "press", ["--key", args.key])
  const entry = await waitForNativeExport(args.exportDir, before.length)
  if (!entry) return null
  const text = await fs.readFile(entry.copiedPath, "utf8").catch(() => "")
  await fs.writeFile(args.outputPath, text, "utf8")
  return text
}

const captureTmuxPaneText = async (target: string): Promise<{ visible: string; scrollback: string } | null> => {
  const visible = await execCapture("tmux", ["capture-pane", "-pt", target], process.env).catch(() => null)
  const scrollback = await execCapture("tmux", ["capture-pane", "-pt", target, "-S", "-400"], process.env).catch(() => null)
  if (!visible && !scrollback) return null
  return {
    visible: visible?.stdout ?? "",
    scrollback: scrollback?.stdout ?? visible?.stdout ?? "",
  }
}

const captureSnapshot = async (
  label: string,
  display: string,
  observerTextDir: string,
  observerPngDir: string,
  captureScreenshots: boolean,
  tmuxTarget?: string | null,
  nativeExportDir?: string | null,
): Promise<{
  text: string
  scrollbackText: string
  screenshotPath?: string | null
  x11WindowId?: string | null
  candidateCount: number
}> => {
  const sanitized = sanitizeLabel(label)
  const treePath = path.join(observerTextDir, `${sanitized}.x11.txt`)
  const summaryPath = path.join(observerTextDir, `${sanitized}.summary.txt`)
  const discovery = await discoverGhosttyWindow(display)
  const screenshotPath = path.join(observerPngDir, `${sanitized}.png`)
  if (captureScreenshots) {
    if (discovery.chosen) {
      const xwdCmd = `xwd -display ${shellQuote(display)} -silent -id ${shellQuote(discovery.chosen.id)} | convert xwd:- ${shellQuote(screenshotPath)}`
      const xwdCapture = await execCapture("bash", ["-lc", xwdCmd], process.env).catch(() => ({ stdout: "", stderr: "", exitCode: 1 }))
      if (xwdCapture.exitCode !== 0) {
        const crop = `${discovery.chosen.width}x${discovery.chosen.height}+${Math.max(0, discovery.chosen.x)}+${Math.max(0, discovery.chosen.y)}`
        await execCapture("import", ["-display", display, "-window", "root", "-crop", crop, screenshotPath], process.env).catch(() => ({ stdout: "", stderr: "", exitCode: 1 }))
      }
    } else {
      await execCapture("import", ["-display", display, "-window", "root", screenshotPath], process.env).catch(() => ({ stdout: "", stderr: "", exitCode: 1 }))
    }
  }
  await fs.writeFile(treePath, discovery.tree, "utf8")
  const tmuxCapture = tmuxTarget ? await captureTmuxPaneText(tmuxTarget) : null
  const tmuxVisiblePath = tmuxTarget ? path.join(observerTextDir, `${sanitized}.tmux.txt`) : null
  const tmuxScrollbackPath = tmuxTarget ? path.join(observerTextDir, `${sanitized}.tmux_scrollback.txt`) : null
  if (tmuxCapture && tmuxVisiblePath && tmuxScrollbackPath) {
    await fs.writeFile(tmuxVisiblePath, tmuxCapture.visible, "utf8")
    await fs.writeFile(tmuxScrollbackPath, tmuxCapture.scrollback, "utf8")
  }
  const nativeScreenPath = nativeExportDir ? path.join(observerTextDir, `${sanitized}.ghostty_screen.txt`) : null
  const nativeScrollbackPath = nativeExportDir ? path.join(observerTextDir, `${sanitized}.ghostty_scrollback.txt`) : null
  const nativeScreenText =
    nativeExportDir && discovery.chosen && nativeScreenPath
      ? await captureNativeGhosttyFile({
          display,
          windowId: discovery.chosen.id,
          exportDir: nativeExportDir,
          key: "ctrl+shift+t",
          outputPath: nativeScreenPath,
        })
      : null
  const nativeScrollbackText =
    nativeExportDir && discovery.chosen && nativeScrollbackPath
      ? await captureNativeGhosttyFile({
          display,
          windowId: discovery.chosen.id,
          exportDir: nativeExportDir,
          key: "ctrl+shift+o",
          outputPath: nativeScrollbackPath,
        })
      : null
  const summaryLines = [
    `label: ${label}`,
    `x11_candidate_window_count: ${discovery.windows.length}`,
    `x11_candidate_windows: ${discovery.windows.map((entry) => `${entry.id}:${entry.width}x${entry.height}@${entry.x},${entry.y}`).join(", ")}`,
    `x11_capture_window: ${discovery.chosen?.id ?? "root"}`,
    captureScreenshots ? `screenshot: ${path.basename(screenshotPath)}` : "screenshot: disabled",
    tmuxTarget ? `tmux_target: ${tmuxTarget}` : "tmux_target: none",
    nativeScreenPath ? `ghostty_screen_file: ${path.basename(nativeScreenPath)}` : "ghostty_screen_file: none",
    nativeScrollbackPath ? `ghostty_scrollback_file: ${path.basename(nativeScrollbackPath)}` : "ghostty_scrollback_file: none",
    nativeExportDir
      ? `ghostty_native_export_status: screen=${nativeScreenText == null ? "missing" : "ok"} scrollback=${nativeScrollbackText == null ? "missing" : "ok"}`
      : "ghostty_native_export_status: disabled",
  ]
  const summaryText = `${summaryLines.join("\n")}\n`
  await fs.writeFile(summaryPath, summaryText, "utf8")
  const nativeMissingPayload = nativeExportDir
    ? [
        "[ghostty-native-export-missing]",
        "Native Ghostty export was requested, but write_screen_file/write_scrollback_file did not produce text.",
        "This capture is not allowed to fall back to X11 window-tree text for claim-bearing native Ghostty gates.",
      ].join("\n")
    : null
  const visiblePayload = nativeExportDir
    ? nativeScreenText ?? tmuxCapture?.visible ?? nativeMissingPayload ?? discovery.tree
    : tmuxCapture?.visible ?? discovery.tree
  const scrollbackPayload = nativeExportDir
    ? nativeScrollbackText ?? nativeScreenText ?? tmuxCapture?.scrollback ?? nativeMissingPayload ?? discovery.tree
    : tmuxCapture?.scrollback ?? discovery.tree
  const visibleText = `${summaryText}\n${visiblePayload}`
  const scrollbackText = `${summaryText}\n${scrollbackPayload}`
  return {
    text: visibleText,
    scrollbackText,
    screenshotPath: captureScreenshots ? screenshotPath : null,
    x11WindowId: discovery.chosen?.id ?? null,
    candidateCount: discovery.windows.length,
  }
}

const ensureWindowId = async (display: string, currentWindowId: string | null): Promise<string | null> => {
  if (currentWindowId) {
    return currentWindowId
  }
  const startedAt = Date.now()
  while (Date.now() - startedAt < 5_000) {
    const discovery = await discoverGhosttyWindow(display)
    if (discovery.chosen) {
      return discovery.chosen.id
    }
    await sleep(100)
  }
  return null
}

const runX11Control = async (
  display: string,
  windowId: string,
  command: "focus" | "click" | "type" | "press" | "resize",
  extraArgs: string[] = [],
): Promise<void> => {
  const result = await execCapture(
    "python3",
    [X11_CONTROL_SCRIPT, "--display", display, "--window", windowId, command, ...extraArgs],
    process.env,
  )
  if (result.exitCode !== 0) {
    throw new Error(result.stderr.trim() || result.stdout.trim() || `x11 control failed for ${command}`)
  }
}

export const runGhosttyObserverHarness = async (
  options: GhosttyObserverHarnessOptions,
): Promise<GhosttyObserverHarnessResult> => {
  const runtimeDir = path.resolve(options.runtimeDir)
  const observerRuntimeDir = path.join(runtimeDir, "observer_runtime")
  const observerTextDir = path.join(runtimeDir, "observer_text")
  const observerPngDir = path.join(runtimeDir, "observer_png")
  await fs.mkdir(observerRuntimeDir, { recursive: true })
  await fs.mkdir(observerTextDir, { recursive: true })
  await fs.mkdir(observerPngDir, { recursive: true })

  const startedAt = Date.now()
  let xvfbChild: ChildProcess | null = null
  let sessionChild: ChildProcess | null = null
  let display = ""
  const snapshots: SnapshotEntry[] = []
  const frames: GhosttyObserverFrame[] = []
  let finalX11WindowId: string | null = null
  let launchStatus: GhosttyObserverHarnessResult["metadata"]["launchStatus"] = "failed"
  let maxCandidateCount = 0
  let finalScrollbackBuffer = ""
  let interactionPrimed = false
  const command = `${options.command}${options.configPath ? ` --config ${shellQuote(options.configPath)}` : ""}`
  const launchCommand = command
  const launchScriptPath = path.join(observerRuntimeDir, "ghostty-session.sh")
  const xvfbMetaPath = path.join(observerRuntimeDir, "xvfb-display.txt")
  const sessionStdoutPath = path.join(observerRuntimeDir, "session.stdout.log")
  const sessionStderrPath = path.join(observerRuntimeDir, "session.stderr.log")

  try {
    const xvfb = await startXvfb(runtimeDir, options.cols, options.rows)
    xvfbChild = xvfb.child
    display = xvfb.display
    await fs.writeFile(xvfbMetaPath, `${display}\n`, "utf8")
    await buildLaunchScript(launchScriptPath, options, launchCommand, observerRuntimeDir, display)
    const sessionEnv = {
      ...process.env,
      DISPLAY: display,
      XDG_RUNTIME_DIR: process.env.XDG_RUNTIME_DIR ?? "/run/user/1041",
    }
    sessionChild = spawn("dbus-run-session", ["--", "bash", launchScriptPath], {
      cwd: options.cwd ?? process.cwd(),
      env: sessionEnv,
      stdio: ["ignore", "pipe", "pipe"],
    })
    let sessionStdout = ""
    let sessionStderr = ""
    sessionChild.stdout?.on("data", (chunk) => {
      sessionStdout += String(chunk)
    })
    sessionChild.stderr?.on("data", (chunk) => {
      sessionStderr += String(chunk)
    })

    await sleep(DEFAULT_GNOME_SHELL_BOOT_MS + 4000)
    const nativeExportDir =
      options.envOverrides?.[NATIVE_EXPORT_FLAG] === "1"
        ? path.join(observerRuntimeDir, "ghostty-native-export")
        : null
    const observeText = async (label: string): Promise<{ text: string; scrollbackText: string; candidateCount: number; x11WindowId?: string | null }> => {
      const result = await captureSnapshot(
        label,
        display,
        observerTextDir,
        observerPngDir,
        options.captureScreenshots !== false,
        options.envOverrides?.BREADBOARD_GHOSTTY_TMUX_TARGET ?? process.env.BREADBOARD_GHOSTTY_TMUX_TARGET ?? null,
        nativeExportDir,
      )
      finalX11WindowId = result.x11WindowId ?? finalX11WindowId
      maxCandidateCount = Math.max(maxCandidateCount, result.candidateCount)
      finalScrollbackBuffer = result.scrollbackText
      if (result.candidateCount > 0) {
        launchStatus = "success"
      } else if (launchStatus === "failed") {
        launchStatus = "partial_success"
      }
      snapshots.push({ label, cleaned: result.text })
      frames.push({
        timestamp: Date.now(),
        label,
        text: result.text,
        screenshotPath: result.screenshotPath ?? null,
      })
      return result
    }
    const waitForOutput = async (text: string, timeoutMs = 10_000) => {
      const startedAt = Date.now()
      let attempt = 0
      while (Date.now() - startedAt < timeoutMs) {
        const observed = await observeText(`waitFor-${attempt}`)
        if (`${observed.text}\n${observed.scrollbackText}`.includes(text)) return
        attempt += 1
        await sleep(250)
      }
      throw new Error(`Timed out waiting for Ghostty output containing ${JSON.stringify(text)}`)
    }
    const waitForComposerReady = async (timeoutMs = 10_000) => {
      const startedAt = Date.now()
      let attempt = 0
      while (Date.now() - startedAt < timeoutMs) {
        const observed = await observeText(`waitForComposerReady-${attempt}`)
        if (includesComposerReady(`${observed.text}\n${observed.scrollbackText}`)) return
        attempt += 1
        await sleep(250)
      }
      throw new Error("Timed out waiting for Ghostty composer readiness")
    }
    const supportedActions = new Set<Step["action"]>([
      "wait",
      "waitFor",
      "waitForComposerReady",
      "snapshot",
      "log",
      "type",
      "paste",
      "press",
      "resize",
    ])
    for (const [stepIndex, step] of options.steps.entries()) {
      if (!supportedActions.has(step.action)) {
        throw new Error(`Ghostty lane only supports wait/snapshot/log/type/paste/press/resize steps; received ${step.action}`)
      }
      finalX11WindowId = await ensureWindowId(display, finalX11WindowId)
      options.onStepStart?.({ stepIndex, step, display, x11WindowId: finalX11WindowId })
      if (step.action === "wait") {
        await sleep(step.ms)
        continue
      }
      if (step.action === "waitFor") {
        await waitForOutput(step.text, step.timeoutMs)
        continue
      }
      if (step.action === "waitForComposerReady") {
        await waitForComposerReady(step.timeoutMs)
        continue
      }
      if (step.action === "log") {
        const logPath = path.join(observerTextDir, `${String(stepIndex + 1).padStart(2, "0")}-log.txt`)
        await fs.writeFile(logPath, `${step.message}\n`, "utf8")
        continue
      }
      if (step.action === "type" || step.action === "paste") {
        if (!finalX11WindowId) {
          throw new Error("Ghostty window was not discoverable before type/paste")
        }
        if (!interactionPrimed) {
          await runX11Control(display, finalX11WindowId, "click")
          await runX11Control(display, finalX11WindowId, "focus")
          interactionPrimed = true
          await sleep(200)
        }
        await runX11Control(display, finalX11WindowId, "type", ["--text", step.text])
        if (step.action === "type" && step.typingDelayMs) {
          await sleep(step.typingDelayMs)
        } else {
          await sleep(120)
        }
        continue
      }
      if (step.action === "press") {
        if (!finalX11WindowId) {
          throw new Error("Ghostty window was not discoverable before key press")
        }
        if (!interactionPrimed) {
          await runX11Control(display, finalX11WindowId, "click")
          await runX11Control(display, finalX11WindowId, "focus")
          interactionPrimed = true
          await sleep(200)
        }
        const repeat = step.repeat ?? 1
        for (let index = 0; index < repeat; index += 1) {
          await runX11Control(display, finalX11WindowId, "press", ["--key", step.key])
          await sleep(step.delayMs ?? 80)
        }
        continue
      }
      if (step.action === "resize") {
        if (finalX11WindowId) {
          const size = computePixelSize(step.cols, step.rows)
          await runX11Control(display, finalX11WindowId, "resize", ["--width", String(size.width), "--height", String(size.height)])
        } else {
          const size = computePixelSize(step.cols, step.rows)
          await execCapture("xrandr", ["--display", display, "--fb", `${size.width}x${size.height}`], process.env).catch(() => ({ stdout: "", stderr: "", exitCode: 1 }))
        }
        await sleep(step.delayMs ?? 1500)
        continue
      }
      if (step.action === "snapshot" && finalX11WindowId && !interactionPrimed) {
        await runX11Control(display, finalX11WindowId, "click")
        await runX11Control(display, finalX11WindowId, "focus")
        interactionPrimed = true
        await sleep(400)
      }
      const snapshotLabel = "label" in step ? step.label : step.action
      await observeText(snapshotLabel)
    }

    await fs.writeFile(sessionStdoutPath, sessionStdout, "utf8")
    await fs.writeFile(sessionStderrPath, sessionStderr, "utf8")
  } catch (error) {
    const finishedAt = Date.now()
    const plainBuffer = frames.at(-1)?.text ?? "ghostty observer failed before any snapshot"
    const result: GhosttyObserverHarnessResult = {
      snapshots,
      plainBuffer,
      scrollbackBuffer: finalScrollbackBuffer || plainBuffer,
      frames,
      metadata: {
        startedAt,
        finishedAt,
        durationMs: finishedAt - startedAt,
        command: options.command,
        cols: options.cols,
        rows: options.rows,
        display,
        runtimeDir,
        x11WindowId: finalX11WindowId,
        launchStatus,
        x11CandidateWindowCount: maxCandidateCount,
      },
    }
    throw new GhosttyObserverHarnessError(error instanceof Error ? error.message : String(error), result, error)
  } finally {
    if (sessionChild && sessionChild.exitCode == null) {
      sessionChild.kill("SIGTERM")
      await sleep(1500)
      if (sessionChild.exitCode == null) {
        sessionChild.kill("SIGKILL")
      }
    }
    if (xvfbChild && xvfbChild.exitCode == null) {
      xvfbChild.kill("SIGTERM")
      await sleep(500)
      if (xvfbChild.exitCode == null) {
        xvfbChild.kill("SIGKILL")
      }
    }
  }

  const finishedAt = Date.now()
  const plainBuffer = frames.at(-1)?.text ?? "ghostty observer completed without snapshots"
  return {
    snapshots,
    plainBuffer,
    scrollbackBuffer: finalScrollbackBuffer || plainBuffer,
    frames,
    metadata: {
      startedAt,
      finishedAt,
      durationMs: finishedAt - startedAt,
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      display,
      runtimeDir,
      x11WindowId: finalX11WindowId,
      launchStatus,
      x11CandidateWindowCount: maxCandidateCount,
    },
  }
}
