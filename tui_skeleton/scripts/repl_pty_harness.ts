// Legacy-compat PTY lane: keep only for compatibility smoke scripts and ad hoc repro info; prefer spectator + run_stress_bundles for canonical QC.
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import pty from "node-pty"
import stripAnsi from "strip-ansi"
import { HeadlessTerminalMirror } from "./harness/headlessTerminal"
import { COMPOSER_READY_MARKERS } from "./harness/composerReady"
import {
  describeTerminalFailedState,
  matchesStateWaitCriteria,
  type StateDumpRecord,
  type StateWaitCriteria,
} from "./harness/stateDumpWait"

const MAX_BUFFER_SIZE = 200_000
const MAX_PLAIN_BUFFER_SIZE = 40_000
const DEFAULT_WATCHDOG_MS = 60_000
const DEFAULT_SUBMIT_TIMEOUT_MS = 0
const DEFAULT_MAX_DURATION_MS = 180_000

type KeyName =
  | "ctrl+a"
  | "enter"
  | "shift+enter"
  | "escape"
  | "tab"
  | "backspace"
  | "ctrl+backspace"
  | "ctrl+c"
  | "ctrl+d"
  | "ctrl+g"
  | "ctrl+b"
  | "ctrl+k"
  | "ctrl+l"
  | "ctrl+p"
  | "ctrl+r"
  | "ctrl+s"
  | "ctrl+t"
  | "ctrl+o"
  | "ctrl+v"
  | "ctrl+z"
  | "ctrl+y"
  | "alt+r"
  | "ctrl+left"
  | "ctrl+right"
  | "up"
  | "down"
  | "left"
  | "right"

type Step =
  | { readonly action: "wait"; readonly ms: number }
  | { readonly action: "type"; readonly text: string; readonly typingDelayMs?: number }
  | {
      readonly action: "press"
      readonly key: KeyName
      readonly repeat?: number
      readonly delayMs?: number
      readonly expectSubmit?: boolean
    }
  | { readonly action: "snapshot"; readonly label: string; readonly maxLines?: number; readonly mode?: "frame" | "history" }
  | {
      readonly action: "waitFor"
      readonly text: string
      readonly timeoutMs?: number
      readonly mode?: "frame" | "history" | "either"
      readonly fresh?: boolean
    }
  | {
      readonly action: "waitForComposerReady"
      readonly timeoutMs?: number
      readonly mode?: "frame" | "history" | "either"
      readonly fresh?: boolean
    }
  | ({
      readonly action: "waitForState"
    } & StateWaitCriteria)
  | { readonly action: "log"; readonly message: string }
  | { readonly action: "resize"; readonly cols: number; readonly rows: number; readonly delayMs?: number }

interface ScriptDefinition {
  readonly steps: Step[]
}

interface HarnessOptions {
  readonly scriptPath: string
  readonly configPath?: string
  readonly command?: string
  readonly snapshotPath?: string
  readonly rawOutputPath?: string
  readonly baseUrl?: string
  readonly cols: number
  readonly rows: number
  readonly echo: boolean
  readonly watchdogMs: number
  readonly submitTimeoutMs: number
  readonly maxDurationMs: number
}

const MAX_SNAPSHOT_LINES = 120

interface SnapshotEntry {
  readonly label: string
  readonly cleaned: string
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const CURSOR_POSITION_QUERY = "\u001b[6n"
const CURSOR_POSITION_RESPONSE = "\u001b[1;1R"
const PRIMARY_DEVICE_ATTRIBUTES_QUERY = "\u001b[c"
const PRIMARY_DEVICE_ATTRIBUTES_RESPONSE = "\u001b[?62;4;22c"
const OSC_FOREGROUND_QUERY = "\u001b]10;?\u001b\\"
const OSC_FOREGROUND_RESPONSE = "\u001b]10;rgb:ffff/ffff/ffff\u001b\\"
const OSC_BACKGROUND_QUERY = "\u001b]11;?\u001b\\"
const OSC_BACKGROUND_RESPONSE = "\u001b]11;rgb:0000/0000/0000\u001b\\"

const normalizeSteps = (value: unknown): Step[] => {
  if (Array.isArray(value)) {
    return value as Step[]
  }
  if (typeof value === "object" && value !== null && Array.isArray((value as Record<string, unknown>).steps)) {
    return (value as ScriptDefinition).steps
  }
  throw new Error("Script file must be an array or an object with a 'steps' array.")
}

const resolveKey = (key: KeyName): string => {
  switch (key) {
    case "ctrl+a":
      return "\u0001"
    case "enter":
      return "\r"
    case "shift+enter":
      return "\u001b[13;2u"
    case "escape":
      return "\u001b"
    case "tab":
      return "\t"
    case "backspace":
      return "\u007f"
    case "ctrl+backspace":
      return "\u0017"
    case "ctrl+c":
      return "\u0003"
    case "ctrl+d":
      return "\u0004"
    case "ctrl+g":
      return "\u0007"
    case "ctrl+b":
      return "\u0002"
    case "ctrl+l":
      return "\f"
    case "ctrl+p":
      return "\u0010"
    case "ctrl+r":
      return "\u0012"
    case "ctrl+s":
      return "\u0013"
    case "ctrl+k":
      return "\u000b"
    case "ctrl+t":
      return "\u0014"
    case "ctrl+o":
      return "\u000f"
    case "ctrl+v":
      return "\u0016"
    case "ctrl+z":
      return "\u001a"
    case "ctrl+y":
      return "\u0019"
    case "alt+r":
      return "\u001br"
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
    default:
      throw new Error(`Unsupported key: ${key}`)
  }
}

const parseArgs = (): HarnessOptions => {
  const args = process.argv.slice(2)
  let scriptPath: string | undefined
  let configPath: string | undefined
  let command = "node dist/main.js repl --tui classic"
  let snapshotPath: string | undefined
  let rawOutputPath: string | undefined
  let baseUrl = process.env.BREADBOARD_API_URL
  let cols = 120
  let rows = 36
  let echo = false
  let watchdogMs = DEFAULT_WATCHDOG_MS
  let submitTimeoutMs = DEFAULT_SUBMIT_TIMEOUT_MS
  let maxDurationMs = DEFAULT_MAX_DURATION_MS

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--script":
        scriptPath = args[++i]
        break
      case "--config":
        configPath = args[++i]
        break
      case "--cmd":
        command = args[++i]
        break
      case "--snapshots":
        snapshotPath = args[++i]
        break
      case "--raw-output":
        rawOutputPath = args[++i]
        break
      case "--base-url":
        baseUrl = args[++i]
        break
      case "--cols":
        cols = Number(args[++i] ?? cols)
        break
      case "--rows":
        rows = Number(args[++i] ?? rows)
        break
      case "--echo":
        echo = true
        break
      case "--watchdog-ms":
        watchdogMs = Number(args[++i] ?? watchdogMs)
        break
      case "--submit-timeout-ms":
        submitTimeoutMs = Number(args[++i] ?? submitTimeoutMs)
        break
      case "--max-duration-ms":
        maxDurationMs = Number(args[++i] ?? maxDurationMs)
        break
      default:
        break
    }
  }

  if (!scriptPath) {
    throw new Error("--script <path> is required")
  }

  return {
    scriptPath,
    configPath,
    command,
    snapshotPath,
    rawOutputPath,
    baseUrl,
    cols,
    rows,
    echo,
    watchdogMs,
    submitTimeoutMs,
    maxDurationMs,
  }
}

const loadSteps = async (scriptPath: string): Promise<Step[]> => {
  const absolute = path.isAbsolute(scriptPath) ? scriptPath : path.join(process.cwd(), scriptPath)
  const raw = await fs.readFile(absolute, "utf8")
  const parsed = JSON.parse(raw) as unknown
  return normalizeSteps(parsed)
}

const writeSnapshots = async (snapshots: SnapshotEntry[], target?: string) => {
  if (!target) return
  const lines: string[] = []
  for (const snapshot of snapshots) {
    lines.push(`# ${snapshot.label}`)
    lines.push(snapshot.cleaned)
    lines.push("")
  }
  await fs.writeFile(target, lines.join("\n"), "utf8")
}

const writeRawOutput = async (raw: string, target?: string) => {
  if (!target) return
  await fs.writeFile(target, raw, "utf8")
}

const respondToTerminalProbes = (child: pty.IPty, data: string) => {
  // Some TUIs, including Codex, issue terminal capability probes during
  // startup. When the harness runs under node-pty there is no real terminal to
  // answer them, so we emulate the small subset required to unblock the UI.
  if (data.includes(CURSOR_POSITION_QUERY)) {
    child.write(CURSOR_POSITION_RESPONSE)
  }
  if (data.includes(PRIMARY_DEVICE_ATTRIBUTES_QUERY)) {
    child.write(PRIMARY_DEVICE_ATTRIBUTES_RESPONSE)
  }
  if (data.includes(OSC_FOREGROUND_QUERY)) {
    child.write(OSC_FOREGROUND_RESPONSE)
  }
  if (data.includes(OSC_BACKGROUND_QUERY)) {
    child.write(OSC_BACKGROUND_RESPONSE)
  }
}

const run = async () => {
  const options = parseArgs()
  const runStart = Date.now()
  const steps = await loadSteps(options.scriptPath)
  const snapshots: SnapshotEntry[] = []

  const commandParts = options.command.split(" ").filter((part) => part.length > 0)
  const executable = commandParts[0]
  const args = commandParts.slice(1)

  if (options.configPath) {
    args.push("--config", options.configPath)
  }

  const env = { ...process.env }
  if (options.baseUrl) {
    env.BREADBOARD_API_URL = options.baseUrl
  }
  // Keep PTY harness runs hermetic and writable under sandboxed environments by
  // default, but allow real-provider wrapper QC to preserve the caller's home
  // directory so auth/stateful local tooling continues to work.
  const preserveHome = (env.BREADBOARD_PTY_PRESERVE_HOME ?? "").trim().toLowerCase()
  const shouldPreserveHome = preserveHome === "1" || preserveHome === "true" || preserveHome === "yes"
  if (!shouldPreserveHome) {
    env.HOME = env.BREADBOARD_PTY_HOME?.trim() || path.join(process.cwd(), ".tmp_pty_home")
    env.USERPROFILE = env.HOME
  }

  let buffer = ""
  let plainBuffer = ""
  let lastOutput = Date.now()
  let observedUserLines = 0
  let hasPendingInput = false
  let pendingSubmit: { readonly deadline: number; readonly userLines: number } | null = null
  let currentInput = ""
  const terminalMirror = new HeadlessTerminalMirror(options.cols, options.rows)

  const child = pty.spawn(executable, args, {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: process.cwd(),
    env,
  })

  const appendPlain = (chunk: string) => {
    const normalized = chunk
      .replaceAll(CURSOR_POSITION_RESPONSE, "")
      .replaceAll(PRIMARY_DEVICE_ATTRIBUTES_RESPONSE, "")
      .replaceAll(OSC_FOREGROUND_RESPONSE, "")
      .replaceAll(OSC_BACKGROUND_RESPONSE, "")
    plainBuffer = (plainBuffer + normalized).slice(-MAX_PLAIN_BUFFER_SIZE)
  }

  const isInputLocked = (): boolean => {
    const tail = plainBuffer.slice(-4000)
    return tail.includes("Input locked — use the active modal controls.")
  }

  const updateUserLineStats = () => {
    const matches = plainBuffer.match(/USER\s+/g)
    const currentCount = matches ? matches.length : 0
    if (currentCount > observedUserLines) {
      observedUserLines = currentCount
    }
    if (pendingSubmit && currentCount > pendingSubmit.userLines) {
      pendingSubmit = null
      hasPendingInput = false
    }
  }

  const onData = (data: string) => {
    lastOutput = Date.now()
    buffer += data
    if (buffer.length > MAX_BUFFER_SIZE) {
      buffer = buffer.slice(buffer.length - MAX_BUFFER_SIZE)
    }
    appendPlain(stripAnsi(data))
    terminalMirror.write(data)
    respondToTerminalProbes(child, data)
    updateUserLineStats()
    if (options.echo) {
      process.stdout.write(data)
    }
  }

  child.onData(onData)

  const checkWatchdog = () => {
    if (options.watchdogMs > 0 && Date.now() - lastOutput > options.watchdogMs) {
      throw new Error(`Watchdog timeout: no terminal output for ${options.watchdogMs}ms`)
    }
  }

  const checkPendingSubmit = () => {
    if (pendingSubmit && options.submitTimeoutMs > 0 && Date.now() > pendingSubmit.deadline) {
      throw new Error(`Prompt submission timeout: no USER line after ${options.submitTimeoutMs}ms`)
    }
  }

  const checkDuration = () => {
    if (options.maxDurationMs > 0 && Date.now() - runStart > options.maxDurationMs) {
      throw new Error(`Harness timeout: exceeded ${options.maxDurationMs}ms total runtime`)
    }
  }

  const checkGuards = () => {
    checkWatchdog()
    checkPendingSubmit()
    checkDuration()
  }

  const readLatestStateDumpRecord = async (): Promise<StateDumpRecord | null> => {
    const target = (env.BREADBOARD_STATE_DUMP_PATH ?? "").trim()
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

  const waitWithGuards = async (ms: number) => {
    if (ms <= 0) return
    const slice = 100
    let waited = 0
    while (waited < ms) {
      const chunk = Math.min(slice, ms - waited)
      await sleep(chunk)
      waited += chunk
      checkGuards()
    }
  }

  const waitForOutput = async (
    text: string,
    timeoutMs = 10_000,
    mode: "frame" | "history" | "either" = "either",
    fresh = false,
  ) => {
    const start = Date.now()
    const baselineBufferLength = buffer.length
    const baselinePlainLength = plainBuffer.length
    await terminalMirror.flush()
    const baselineViewportText = terminalMirror.getViewportText()
    const baselineViewportHasText = baselineViewportText.includes(text)
    let frameChanged = false
    let historyChanged = false
    let frameObservedWithoutText = !baselineViewportHasText

    const historyHasFreshMatch = () => {
      if (!fresh) {
        return buffer.includes(text) || plainBuffer.includes(text)
      }
      const freshBuffer = buffer.slice(baselineBufferLength)
      const freshPlain = plainBuffer.slice(baselinePlainLength)
      historyChanged = historyChanged || freshBuffer.length > 0 || freshPlain.length > 0
      return (freshBuffer.includes(text) || freshPlain.includes(text)) && historyChanged
    }

    const frameHasFreshMatch = async () => {
      await terminalMirror.flush()
      const viewportText = terminalMirror.getViewportText()
      frameChanged = frameChanged || viewportText !== baselineViewportText
      const hasText = viewportText.includes(text)
      if (!hasText) {
        frameObservedWithoutText = true
      }
      if (!fresh) {
        return hasText
      }
      if (!hasText || !frameChanged) {
        return false
      }
      return baselineViewportHasText ? frameObservedWithoutText : true
    }

    while (Date.now() - start < timeoutMs) {
      if (mode === "frame") {
        if (await frameHasFreshMatch()) return
      } else if (mode === "history") {
        if (historyHasFreshMatch()) return
      } else if (historyHasFreshMatch()) {
        return
      } else if (await frameHasFreshMatch()) {
        return
      }
      await sleep(50)
      checkGuards()
    }
    throw new Error(`Timed out waiting for output containing "${text}" in ${mode} mode${fresh ? " after a fresh state change" : ""}`)
  }

  const waitForAnyOutput = async (
    texts: readonly string[],
    timeoutMs = 10_000,
    mode: "frame" | "history" | "either" = "either",
    fresh = false,
  ) => {
    const start = Date.now()
    const baselineBufferLength = buffer.length
    const baselinePlainLength = plainBuffer.length
    await terminalMirror.flush()
    const baselineViewportText = terminalMirror.getViewportText()
    const baselineViewportHasText = texts.some((text) => baselineViewportText.includes(text))
    let frameChanged = false
    let historyChanged = false
    let frameObservedWithoutText = !baselineViewportHasText

    const historyHasFreshMatch = () => {
      if (!fresh) {
        return texts.some((text) => buffer.includes(text) || plainBuffer.includes(text))
      }
      const freshBuffer = buffer.slice(baselineBufferLength)
      const freshPlain = plainBuffer.slice(baselinePlainLength)
      historyChanged = historyChanged || freshBuffer.length > 0 || freshPlain.length > 0
      return texts.some((text) => freshBuffer.includes(text) || freshPlain.includes(text)) && historyChanged
    }

    const frameHasFreshMatch = async () => {
      await terminalMirror.flush()
      const viewportText = terminalMirror.getViewportText()
      frameChanged = frameChanged || viewportText !== baselineViewportText
      const hasText = texts.some((text) => viewportText.includes(text))
      if (!hasText) {
        frameObservedWithoutText = true
      }
      if (!fresh) {
        return hasText
      }
      if (!hasText || !frameChanged) {
        return false
      }
      return baselineViewportHasText ? frameObservedWithoutText : true
    }

    while (Date.now() - start < timeoutMs) {
      if (mode === "frame") {
        if (await frameHasFreshMatch()) return
      } else if (mode === "history") {
        if (historyHasFreshMatch()) return
      } else if (historyHasFreshMatch()) {
        return
      } else if (await frameHasFreshMatch()) {
        return
      }
      await sleep(50)
      checkGuards()
    }
    throw new Error(`Timed out waiting for output containing one of [${texts.join(', ')}] in ${mode} mode${fresh ? " after a fresh state change" : ""}`)
  }

  const waitForComposerReady = async (
    timeoutMs = 10_000,
    mode: "frame" | "history" | "either" = "either",
    fresh = false,
  ) => waitForAnyOutput(COMPOSER_READY_MARKERS, timeoutMs, mode, fresh)

  const waitForState = async (
    criteria: Extract<Step, { readonly action: "waitForState" }>,
  ) => {
    const timeoutMs = criteria.timeoutMs ?? 10_000
    const start = Date.now()
    const baseline = await readLatestStateDumpRecord()
    const baselineTimestamp = baseline?.timestamp ?? 0

    while (Date.now() - start < timeoutMs) {
      const latest = await readLatestStateDumpRecord()
      if (matchesStateWaitCriteria(latest, criteria, baselineTimestamp)) return
      const terminalFailure = describeTerminalFailedState(latest, criteria, baselineTimestamp)
      if (terminalFailure) {
        throw new Error(`Terminal state reached before matching waitForState criteria: ${terminalFailure}`)
      }
      await sleep(50)
      checkGuards()
    }
    throw new Error(`Timed out waiting for matching state criteria after ${timeoutMs}ms`)
  }

  const takeSnapshot = async (label: string, maxLines?: number, mode: "frame" | "history" = "frame") => {
    if (mode === "frame") {
      // PTY close-path redraws can land a few milliseconds after the wait
      // condition is satisfied. Give the terminal mirror one bounded settle
      // cycle before reading the viewport so snapshots reflect the latest frame.
      await terminalMirror.flush()
      await sleep(60)
      await terminalMirror.flush()
    }
    const cap = typeof maxLines === "number" && maxLines > 0 ? Math.floor(maxLines) : MAX_SNAPSHOT_LINES
    const plain = mode === "history" ? terminalMirror.getBufferText(cap) : terminalMirror.getViewportText()
    const lines = plain.split(/\r?\n/)
    const trimmed = lines.slice(-cap).join("\n")
    const promptLine = [...lines]
      .reverse()
      .find((line) => line.trimStart().startsWith("›"))
    const payload = promptLine ? `${trimmed}\n\nPrompt: ${promptLine.trimStart()}` : trimmed
    snapshots.push({
      label,
      cleaned: payload,
    })
  }

  const gracefulShutdown = async () => {
    try {
      child.write(resolveKey("ctrl+c"))
    } catch {
      // ignore
    }
    await sleep(250)
    try {
      child.kill()
    } catch {
      // ignore
    }
  }

  let runError: unknown = null
  try {
    for (const step of steps) {
      switch (step.action) {
        case "wait":
          await waitWithGuards(step.ms)
          break
        case "type":
          if (step.text.length > 0) {
            hasPendingInput = true
            currentInput += step.text
          }
          for (const char of step.text) {
            child.write(char)
            if (step.typingDelayMs && step.typingDelayMs > 0) {
              await sleep(step.typingDelayMs)
            }
            checkGuards()
          }
          break
        case "press": {
          const sequence = resolveKey(step.key)
          const repeat = step.repeat ?? 1
          for (let i = 0; i < repeat; i += 1) {
            child.write(sequence)
            if (step.key === "backspace") {
              currentInput = currentInput.slice(0, -1)
            }
            if (step.key === "ctrl+backspace") {
              currentInput = ""
            }
            if (step.key === "enter") {
              const trimmed = currentInput.trim()
              const isSlashCommand = trimmed.startsWith("/")
              const inputLocked = isInputLocked()
              const shouldExpectSubmit = step.expectSubmit ?? true
              if (
                shouldExpectSubmit &&
                options.submitTimeoutMs > 0 &&
                hasPendingInput &&
                !pendingSubmit &&
                !isSlashCommand &&
                !inputLocked
              ) {
                pendingSubmit = {
                  deadline: Date.now() + options.submitTimeoutMs,
                  userLines: observedUserLines,
                }
              }
              currentInput = ""
              hasPendingInput = false
            }
            if (step.delayMs && step.delayMs > 0) {
              await sleep(step.delayMs)
            }
            checkGuards()
          }
          break
        }
        case "snapshot":
          await takeSnapshot(step.label, step.maxLines, step.mode ?? "frame")
          break
        case "resize":
          child.resize(step.cols, step.rows)
          terminalMirror.resize(step.cols, step.rows)
          if (step.delayMs && step.delayMs > 0) {
            await sleep(step.delayMs)
          }
          checkGuards()
          break
        case "waitFor":
          await waitForOutput(step.text, step.timeoutMs, step.mode ?? "either", step.fresh ?? false)
          break
        case "waitForComposerReady":
          await waitForComposerReady(step.timeoutMs, step.mode ?? "either", step.fresh ?? false)
          break
        case "waitForState":
          await waitForState(step)
          break
        case "log":
          console.log(`[script] ${step.message}`)
          break
        default:
          throw new Error(`Unsupported action ${(step as Step).action}`)
      }
    }
    while (pendingSubmit) {
      await sleep(50)
      checkPendingSubmit()
    }
  } catch (error) {
    runError = error
  } finally {
    if (runError) {
      try {
        await takeSnapshot("failure-final-frame", MAX_SNAPSHOT_LINES, "frame")
        await takeSnapshot("failure-final-history", 1300, "history")
      } catch {
        // Preserve the original failure; snapshots are best-effort diagnostics.
      }
    }
    await gracefulShutdown()
  }

  await writeSnapshots(snapshots, options.snapshotPath)
  await writeRawOutput(buffer, options.rawOutputPath)

  console.log(`Captured ${snapshots.length} snapshot(s).`)
  if (options.snapshotPath) {
    console.log(`Snapshots written to ${options.snapshotPath}`)
  }
  if (options.rawOutputPath) {
    console.log(`Raw output written to ${options.rawOutputPath}`)
  }
  if (runError) {
    throw runError
  }
}

run().catch((error) => {
  console.error("Harness failed:", error)
  process.exitCode = 1
})
