import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

const MAX_BUFFER_SIZE = 200_000
const MAX_PLAIN_BUFFER_SIZE = 40_000
const DEFAULT_WATCHDOG_MS = 60_000
const DEFAULT_SUBMIT_TIMEOUT_MS = 8_000
const DEFAULT_MAX_DURATION_MS = 180_000

type KeyName =
  | "enter"
  | "escape"
  | "tab"
  | "backspace"
  | "ctrl+backspace"
  | "ctrl+c"
  | "ctrl+d"
  | "ctrl+l"
  | "ctrl+t"
  | "ctrl+o"
  | "ctrl+v"
  | "ctrl+left"
  | "ctrl+right"
  | "up"
  | "down"
  | "left"
  | "right"

type Step =
  | { readonly action: "wait"; readonly ms: number }
  | { readonly action: "type"; readonly text: string; readonly typingDelayMs?: number }
  | { readonly action: "press"; readonly key: KeyName; readonly repeat?: number; readonly delayMs?: number }
  | { readonly action: "snapshot"; readonly label: string }
  | { readonly action: "waitFor"; readonly text: string; readonly timeoutMs?: number }
  | { readonly action: "log"; readonly message: string }

interface ScriptDefinition {
  readonly steps: Step[]
}

interface HarnessOptions {
  readonly scriptPath: string
  readonly configPath?: string
  readonly command?: string
  readonly snapshotPath?: string
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
    case "enter":
      return "\r"
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
    case "ctrl+l":
      return "\f"
    case "ctrl+k":
      return "\u000b"
    case "ctrl+t":
      return "\u0014"
    case "ctrl+o":
      return "\u000f"
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
    default:
      throw new Error(`Unsupported key: ${key}`)
  }
}

const parseArgs = (): HarnessOptions => {
  const args = process.argv.slice(2)
  let scriptPath: string | undefined
  let configPath: string | undefined
  let command = "node dist/main.js repl"
  let snapshotPath: string | undefined
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

  let buffer = ""
  let plainBuffer = ""
  let lastOutput = Date.now()
  let observedUserLines = 0
  let hasPendingInput = false
  let pendingSubmit: { readonly deadline: number; readonly userLines: number } | null = null
  let currentInput = ""

  const child = pty.spawn(executable, args, {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: process.cwd(),
    env,
  })

  const appendPlain = (chunk: string) => {
    plainBuffer = (plainBuffer + chunk).slice(-MAX_PLAIN_BUFFER_SIZE)
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

  const waitForOutput = async (text: string, timeoutMs = 10_000) => {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      if (buffer.includes(text) || plainBuffer.includes(text)) return
      await sleep(50)
      checkGuards()
    }
    throw new Error(`Timed out waiting for output containing "${text}"`)
  }

  const extractLatestFrame = (input: string): string => {
    const markers = ["\u001b[2J", "\u001bc", "\u001b[H"]
    let index = -1
    for (const marker of markers) {
      const candidate = input.lastIndexOf(marker)
      if (candidate > index) {
        index = candidate
      }
    }
    if (index >= 0) {
      return input.slice(index)
    }
    return input
  }

  const takeSnapshot = (label: string) => {
    const frame = extractLatestFrame(buffer)
    const plain = stripAnsi(frame)
    const lines = plain.split(/\r?\n/)
    const trimmed = lines.slice(-MAX_SNAPSHOT_LINES).join("\n")
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
              if (options.submitTimeoutMs > 0 && hasPendingInput && !pendingSubmit && !isSlashCommand && !inputLocked) {
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
          takeSnapshot(step.label)
          break
        case "waitFor":
          await waitForOutput(step.text, step.timeoutMs)
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
  } finally {
    await gracefulShutdown()
  }

  await writeSnapshots(snapshots, options.snapshotPath)

  console.log(`Captured ${snapshots.length} snapshot(s).`)
  if (options.snapshotPath) {
    console.log(`Snapshots written to ${options.snapshotPath}`)
  }
}

run().catch((error) => {
  console.error("Harness failed:", error)
  process.exitCode = 1
})
