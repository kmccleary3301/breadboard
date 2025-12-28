import crypto from "node:crypto"
import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import pty from "node-pty"
import stripAnsi from "strip-ansi"
import { PNG } from "pngjs"
import { decode as decodeJpeg } from "jpeg-js"

export const MAX_BUFFER_SIZE = 200_000
export const MAX_PLAIN_BUFFER_SIZE = 40_000
export const DEFAULT_WATCHDOG_MS = 60_000
export const DEFAULT_SUBMIT_TIMEOUT_MS = 8_000
export const DEFAULT_MAX_DURATION_MS = 180_000
const MAX_SNAPSHOT_LINES = 120
const MAX_FRAME_ENTRIES = 5_000

export type KeyName =
  | "enter"
  | "escape"
  | "tab"
  | "backspace"
  | "ctrl+backspace"
  | "ctrl+c"
  | "ctrl+d"
  | "ctrl+k"
  | "ctrl+l"
  | "ctrl+o"
  | "ctrl+t"
  | "ctrl+v"
  | "ctrl+left"
  | "ctrl+right"
  | "up"
  | "down"
  | "left"
  | "right"
  | "pageup"
  | "pagedown"
  | "home"
  | "end"

export type Step =
  | { readonly action: "wait"; readonly ms: number }
  | { readonly action: "type"; readonly text: string; readonly typingDelayMs?: number }
  | { readonly action: "press"; readonly key: KeyName; readonly repeat?: number; readonly delayMs?: number }
  | { readonly action: "snapshot"; readonly label: string }
  | { readonly action: "waitFor"; readonly text: string; readonly timeoutMs?: number }
  | { readonly action: "log"; readonly message: string }
  | { readonly action: "resize"; readonly cols: number; readonly rows: number; readonly delayMs?: number }

interface ScriptDefinition {
  readonly steps: Step[]
}

export interface WinchEvent {
  readonly delayMs?: number
  readonly cols: number
  readonly rows: number
}

export interface SnapshotEntry {
  readonly label: string
  readonly cleaned: string
}

export interface FrameEntry {
  readonly timestamp: number
  readonly chunk: string
}

export interface ClipboardPayload {
  readonly source: "inline" | "file"
  readonly text: string
  readonly path?: string
}

export interface ClipboardMetadata {
  readonly source: "inline" | "file"
  readonly length: number
  readonly sha256: string
  readonly path?: string
  readonly mime?: string
  readonly bytes?: number
  readonly averageColor?: string
}

export interface SpectatorHarnessOptions {
  readonly steps: Step[]
  readonly command: string
  readonly configPath?: string
  readonly baseUrl?: string
  readonly cols: number
  readonly rows: number
  readonly echo?: boolean
  readonly watchdogMs?: number
  readonly submitTimeoutMs?: number
  readonly maxDurationMs?: number
  readonly recordFrames?: boolean
  readonly clipboardPayload?: ClipboardPayload
  readonly onInput?: (entry: Record<string, unknown>) => void
  readonly winchEvents?: ReadonlyArray<WinchEvent> | null
  readonly attachmentSummaryPath?: string
  readonly stateDumpPath?: string
  readonly stateDumpMode?: "summary" | "full"
  readonly stateDumpRateMs?: number
  readonly envOverrides?: Record<string, string>
}

export interface SpectatorHarnessResult {
  readonly snapshots: SnapshotEntry[]
  readonly rawBuffer: string
  readonly plainBuffer: string
  readonly frames: FrameEntry[]
  readonly clipboard?: ClipboardMetadata
  readonly metadata: {
    readonly startedAt: number
    readonly finishedAt: number
    readonly durationMs: number
    readonly watchdogMs: number
    readonly submitTimeoutMs: number
    readonly maxDurationMs: number
    readonly command: string
    readonly cols: number
    readonly rows: number
    readonly stepCount: number
    readonly resizeStats?:
      | {
          readonly count: number
          readonly minCols: number
          readonly maxCols: number
          readonly minRows: number
          readonly maxRows: number
          readonly burstMs: number
          readonly firstEventOffsetMs: number | null
          readonly lastEventOffsetMs: number | null
        }
      | undefined
  }
}

export class SpectatorHarnessError extends Error {
  readonly result: SpectatorHarnessResult

  constructor(message: string, result: SpectatorHarnessResult, cause?: unknown) {
    super(message)
    this.name = "SpectatorHarnessError"
    this.result = result
    ;(this as { cause?: unknown }).cause = cause
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const parseDataUri = (text: string): { mime: string; buffer: Buffer } | null => {
  const match = /^data:([^;]+);base64,(.+)$/i.exec(text)
  if (!match) return null
  try {
    const [, mime, data] = match
    return { mime, buffer: Buffer.from(data, "base64") }
  } catch {
    return null
  }
}

const toHex = (value: number) => Math.round(value).toString(16).padStart(2, "0")

const computeAverageColor = (buffer: Buffer, mime: string): string | undefined => {
  const lowered = mime.toLowerCase()
  if (!lowered.startsWith("image/")) {
    return undefined
  }
  try {
    if (lowered === "image/png") {
      const png = PNG.sync.read(buffer)
      const totalPixels = png.width * png.height
      if (!totalPixels || !png.data || png.data.length === 0) {
        return undefined
      }
      let r = 0
      let g = 0
      let b = 0
      for (let i = 0; i < png.data.length; i += 4) {
        r += png.data[i]
        g += png.data[i + 1]
        b += png.data[i + 2]
      }
      const avgR = r / totalPixels
      const avgG = g / totalPixels
      const avgB = b / totalPixels
      return `#${toHex(avgR)}${toHex(avgG)}${toHex(avgB)}`
    }
    if (lowered === "image/jpeg" || lowered === "image/jpg") {
      const decoded = decodeJpeg(buffer, { useTArray: true, formatAsRGBA: true })
      const totalPixels = decoded.width * decoded.height
      if (!totalPixels || !decoded.data || decoded.data.length === 0) {
        return undefined
      }
      let r = 0
      let g = 0
      let b = 0
      const data = decoded.data
      for (let i = 0; i < data.length; i += 4) {
        r += data[i]
        g += data[i + 1]
        b += data[i + 2]
      }
      return `#${toHex(r / totalPixels)}${toHex(g / totalPixels)}${toHex(b / totalPixels)}`
    }
  } catch {
    return undefined
  }
  return undefined
}

const digestClipboard = (payload: ClipboardPayload | undefined): ClipboardMetadata | undefined => {
  if (!payload) return undefined
  const sha256 = crypto.createHash("sha256").update(payload.text).digest("hex")
  const metadata: ClipboardMetadata = {
    source: payload.source,
    length: payload.text.length,
    sha256,
    path: payload.path,
  }
  const dataUri = parseDataUri(payload.text)
  if (dataUri) {
    metadata.mime = dataUri.mime
    metadata.bytes = dataUri.buffer.length
    const averageColor = computeAverageColor(dataUri.buffer, dataUri.mime)
    if (averageColor) {
      metadata.averageColor = averageColor
    }
  }
  return metadata
}

export const normalizeSteps = (value: unknown): Step[] => {
  if (Array.isArray(value)) {
    return value as Step[]
  }
  if (typeof value === "object" && value !== null && Array.isArray((value as Record<string, unknown>).steps)) {
    return (value as ScriptDefinition).steps
  }
  throw new Error("Script file must be an array or an object with a 'steps' array.")
}

export const loadStepsFromFile = async (scriptPath: string): Promise<Step[]> => {
  const absolute = path.isAbsolute(scriptPath) ? scriptPath : path.join(process.cwd(), scriptPath)
  const raw = await fs.readFile(absolute, "utf8")
  const parsed = JSON.parse(raw) as unknown
  return normalizeSteps(parsed)
}

const normalizeWinchEvents = (value: unknown): WinchEvent[] => {
  const mapEvent = (entry: any): WinchEvent => {
    if (!entry || typeof entry !== "object") {
      throw new Error("Winch entries must be objects with cols/rows.")
    }
    const cols = Number(entry.cols)
    const rows = Number(entry.rows)
    if (!Number.isFinite(cols) || !Number.isFinite(rows)) {
      throw new Error("Winch entries require numeric cols/rows.")
    }
    return {
      cols,
      rows,
      delayMs: typeof entry.delayMs === "number" ? entry.delayMs : 0,
    }
  }
  if (Array.isArray(value)) {
    return value.map(mapEvent)
  }
  if (typeof value === "object" && value !== null && Array.isArray((value as Record<string, unknown>).events)) {
    return (value as Record<string, unknown>).events.map(mapEvent)
  }
  throw new Error("Winch script must be an array or an object with an 'events' array.")
}

export const loadWinchEventsFromFile = async (scriptPath: string): Promise<WinchEvent[]> => {
  const absolute = path.isAbsolute(scriptPath) ? scriptPath : path.join(process.cwd(), scriptPath)
  const raw = await fs.readFile(absolute, "utf8")
  const parsed = JSON.parse(raw) as unknown
  return normalizeWinchEvents(parsed)
}

export const writeSnapshotFile = async (snapshots: SnapshotEntry[], target?: string) => {
  if (!target) return
  const lines: string[] = []
  for (const snapshot of snapshots) {
    lines.push(`# ${snapshot.label}`)
    lines.push(snapshot.cleaned)
    lines.push("")
  }
  await fs.writeFile(target, lines.join("\n"), "utf8")
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
    case "ctrl+k":
      return "\u000b"
    case "ctrl+l":
      return "\f"
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

export const runSpectatorHarness = async (
  options: SpectatorHarnessOptions,
): Promise<SpectatorHarnessResult> => {
  const runStart = Date.now()
  const snapshots: SnapshotEntry[] = []
  const frames: FrameEntry[] = []
  const logInput = (entry: Record<string, unknown>) => {
    if (!options.onInput) return
    options.onInput({ timestamp: Date.now(), ...entry })
  }

  const commandParts = options.command.split(" ").filter((part) => part.length > 0)
  const executable = commandParts[0]
  const args = commandParts.slice(1)

  if (options.configPath) {
    args.push("--config", options.configPath)
  }

  const env = { ...process.env, ...(options.envOverrides ?? {}) }
  if (options.baseUrl) {
    env.BREADBOARD_API_URL = options.baseUrl
  }
  if (options.clipboardPayload) {
    env.BREADBOARD_FAKE_CLIPBOARD = options.clipboardPayload.text
  }
  if (options.attachmentSummaryPath) {
    env.BREADBOARD_ATTACHMENT_SUMMARY_PATH = options.attachmentSummaryPath
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

  let buffer = ""
  let plainBuffer = ""
  let lastOutput = Date.now()
  let observedUserLines = 0
  let hasPendingInput = false
  let pendingSubmit: { readonly deadline: number; readonly userLines: number } | null = null

  const child = pty.spawn(executable, args, {
    name: "xterm-256color",
    cols: options.cols,
    rows: options.rows,
    cwd: process.cwd(),
    env,
  })
  let didExit = false
  const exitPromise = new Promise<void>((resolve) => {
    child.onExit(() => {
      didExit = true
      resolve()
    })
  })

  const resizeStats = {
    count: 0,
    minCols: options.cols,
    maxCols: options.cols,
    minRows: options.rows,
    maxRows: options.rows,
    firstTimestamp: null as number | null,
    lastTimestamp: null as number | null,
  }

  const applyResize = (cols: number, rows: number) => {
    child.resize(cols, rows)
    logInput({ action: "resize", cols, rows })
    resizeStats.count += 1
    resizeStats.minCols = Math.min(resizeStats.minCols, cols)
    resizeStats.maxCols = Math.max(resizeStats.maxCols, cols)
    resizeStats.minRows = Math.min(resizeStats.minRows, rows)
    resizeStats.maxRows = Math.max(resizeStats.maxRows, rows)
    const now = Date.now()
    if (resizeStats.count === 1) {
      resizeStats.firstTimestamp = now
    }
    resizeStats.lastTimestamp = now
  }

  const appendPlain = (chunk: string) => {
    plainBuffer = (plainBuffer + chunk).slice(-MAX_PLAIN_BUFFER_SIZE)
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
    if (options.recordFrames !== false) {
      frames.push({ timestamp: Date.now(), chunk: data })
      if (frames.length > MAX_FRAME_ENTRIES) {
        frames.splice(0, frames.length - MAX_FRAME_ENTRIES)
      }
    }
  }

  child.onData(onData)

  const watchdogMs = options.watchdogMs ?? DEFAULT_WATCHDOG_MS
  const submitTimeoutMs = options.submitTimeoutMs ?? DEFAULT_SUBMIT_TIMEOUT_MS
  const maxDurationMs = options.maxDurationMs ?? DEFAULT_MAX_DURATION_MS
  const trackSubmission = submitTimeoutMs > 0

  const checkWatchdog = () => {
    if (watchdogMs > 0 && Date.now() - lastOutput > watchdogMs) {
      throw new Error(`Watchdog timeout: no terminal output for ${watchdogMs}ms`)
    }
  }

  const checkPendingSubmit = () => {
    if (!trackSubmission) {
      return
    }
    if (pendingSubmit && Date.now() > pendingSubmit.deadline) {
      throw new Error(`Prompt submission timeout: no USER line after ${submitTimeoutMs}ms`)
    }
  }

  const checkDuration = () => {
    if (maxDurationMs > 0 && Date.now() - runStart > maxDurationMs) {
      throw new Error(`Harness timeout: exceeded ${maxDurationMs}ms total runtime`)
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

  const runWinchScript = async () => {
    if (!options.winchEvents || options.winchEvents.length === 0) return
    for (const event of options.winchEvents) {
      const delay = Math.max(0, event.delayMs ?? 0)
      if (delay > 0) {
        await waitWithGuards(delay)
      }
      applyResize(event.cols, event.rows)
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
      await sleep(60)
      child.write(resolveKey("ctrl+c"))
    } catch {
      // ignore
    }
    await Promise.race([exitPromise, sleep(1_500)])
    if (!didExit) {
      try {
        child.kill()
      } catch {
        // ignore
      }
    }
  }

  let winchPromise: Promise<void> | null = null
  if (options.winchEvents && options.winchEvents.length > 0) {
    winchPromise = runWinchScript().catch((error) => {
      console.warn(`[spectator] winch script error: ${(error as Error).message}`)
    })
  }

  let thrown: unknown = null
  try {
    for (const step of options.steps) {
      switch (step.action) {
        case "wait":
          await waitWithGuards(step.ms)
          break
        case "type":
          if (step.text.length > 0) {
            hasPendingInput = true
          }
          for (const char of step.text) {
            child.write(char)
            logInput({ action: "type", char })
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
            logInput({ action: "press", key: step.key })
            if (trackSubmission && step.key === "enter" && hasPendingInput && !pendingSubmit) {
              pendingSubmit = {
                deadline: Date.now() + submitTimeoutMs,
                userLines: observedUserLines,
              }
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
        case "resize":
          if (step.delayMs && step.delayMs > 0) {
            await waitWithGuards(step.delayMs)
          }
          applyResize(step.cols, step.rows)
          break
        default:
          throw new Error(`Unsupported action ${(step as Step).action}`)
      }
    }
    if (winchPromise) {
      await winchPromise
    }
    if (trackSubmission) {
      while (pendingSubmit) {
        await sleep(50)
        checkPendingSubmit()
      }
    }
  } catch (error) {
    thrown = error
  } finally {
    await gracefulShutdown()
  }

  const finishedAt = Date.now()
  const clipboardMetadata = digestClipboard(options.clipboardPayload)

  const result: SpectatorHarnessResult = {
    snapshots,
    rawBuffer: buffer,
    plainBuffer,
    frames,
    clipboard: clipboardMetadata,
    metadata: {
      startedAt: runStart,
      finishedAt,
      durationMs: finishedAt - runStart,
      watchdogMs,
      submitTimeoutMs,
      maxDurationMs,
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      stepCount: options.steps.length,
      resizeStats:
        resizeStats.count > 0
          ? {
              count: resizeStats.count,
              minCols: resizeStats.minCols,
              maxCols: resizeStats.maxCols,
              minRows: resizeStats.minRows,
              maxRows: resizeStats.maxRows,
              burstMs:
                resizeStats.firstTimestamp != null && resizeStats.lastTimestamp != null
                  ? resizeStats.lastTimestamp - resizeStats.firstTimestamp
                  : 0,
              firstEventOffsetMs:
                resizeStats.firstTimestamp != null ? resizeStats.firstTimestamp - runStart : null,
              lastEventOffsetMs:
                resizeStats.lastTimestamp != null ? resizeStats.lastTimestamp - runStart : null,
            }
          : undefined,
    },
  }

  if (thrown) {
    const message = thrown instanceof Error ? thrown.message : String(thrown)
    throw new SpectatorHarnessError(message, result, thrown)
  }

  return result
}
