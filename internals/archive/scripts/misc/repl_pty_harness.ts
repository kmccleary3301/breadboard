import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import pty from "node-pty"
import stripAnsi from "strip-ansi"

type KeyName =
  | "enter"
  | "escape"
  | "tab"
  | "backspace"
  | "ctrl+backspace"
  | "ctrl+c"
  | "ctrl+d"
  | "ctrl+l"
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

  const child = pty.spawn(executable, args, {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: process.cwd(),
    env,
  })

  const onData = (data: string) => {
    buffer += data
    if (buffer.length > 200_000) {
      buffer = buffer.slice(buffer.length - 200_000)
    }
    if (options.echo) {
      process.stdout.write(data)
    }
  }

  child.onData(onData)

  const waitForOutput = async (text: string, timeoutMs = 10_000) => {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      if (buffer.includes(text)) return
      await sleep(50)
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

  for (const step of steps) {
    switch (step.action) {
      case "wait":
        await sleep(step.ms)
        break
      case "type":
        for (const char of step.text) {
          child.write(char)
          if (step.typingDelayMs && step.typingDelayMs > 0) {
            await sleep(step.typingDelayMs)
          }
        }
        break
      case "press": {
        const sequence = resolveKey(step.key)
        const repeat = step.repeat ?? 1
        for (let i = 0; i < repeat; i += 1) {
          child.write(sequence)
          if (step.delayMs && step.delayMs > 0) {
            await sleep(step.delayMs)
          }
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

  child.write(resolveKey("ctrl+c"))
  await sleep(250)
  child.kill()

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

