import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"
import { runSpectatorHarness, type SpectatorStep } from "./harness/spectator.js"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

interface FuzzOptions {
  readonly iterations: number
  readonly stepsPerIteration: number
  readonly seed: number
  readonly command: string
  readonly configPath: string
  readonly baseUrl: string
  readonly cols: number
  readonly rows: number
  readonly submitTimeoutMs: number | undefined
  readonly artifactDir: string
}

class XorShift32 {
  private state: number

  constructor(seed: number) {
    this.state = seed >>> 0
  }

  next(): number {
    let x = this.state
    x ^= x << 13
    x ^= x >>> 17
    x ^= x << 5
    this.state = x >>> 0
    return (this.state & 0xffffffff) / 0xffffffff
  }

  int(max: number): number {
    return Math.floor(this.next() * max)
  }
}

const DEFAULT_CONFIG = "../agent_configs/opencode_cli_mock_guardrails.yaml"
const DEFAULT_COMMAND = "node dist/main.js repl"
const DEFAULT_BASE_URL = process.env.BREADBOARD_API_URL ?? "http://127.0.0.1:9099"

const KEY_POOL = [
  "backspace",
  "delete",
  "left",
  "right",
  "home",
  "end",
  "ctrl+backspace",
  "ctrl+delete",
  "ctrl+z",
  "ctrl+shift+z",
]

const LETTERS = "abcdefghijklmnopqrstuvwxyz"

interface IterationStats {
  readonly iteration: number
  readonly finalLength: number
  readonly maxLength: number
  readonly typedChars: number
  readonly deleteOps: number
  readonly overDeleteAttempts: number
}

const parseArgs = (): FuzzOptions => {
  const args = process.argv.slice(2)
  let iterations = 10
  let stepsPerIteration = 40
  let seed = Date.now() & 0xffffffff
  let command = DEFAULT_COMMAND
  let configPath = DEFAULT_CONFIG
  let baseUrl = DEFAULT_BASE_URL
  let cols = 120
  let rows = 36
  let submitTimeoutMs: number | undefined
  let artifactDir = path.resolve(__dirname, "../artifacts/key_fuzz")

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--iterations":
        iterations = Number(args[++i])
        break
      case "--steps":
        stepsPerIteration = Number(args[++i])
        break
      case "--seed":
        seed = Number(args[++i]) >>> 0
        break
      case "--cmd":
        command = args[++i]
        break
      case "--config":
        configPath = args[++i]
        break
      case "--base-url":
        baseUrl = args[++i]
        break
      case "--cols":
        cols = Number(args[++i])
        break
      case "--rows":
        rows = Number(args[++i])
        break
      case "--submit-timeout-ms":
        submitTimeoutMs = Number(args[++i])
        break
      case "--artifact-dir":
        artifactDir = path.isAbsolute(args[i + 1]) ? args[i + 1] : path.resolve(args[i + 1])
        i += 1
        break
      default:
        break
    }
  }

  return { iterations, stepsPerIteration, seed, command, configPath, baseUrl, cols, rows, submitTimeoutMs, artifactDir }
}

const randomText = (length: number, rng: XorShift32) => {
  let result = ""
  for (let i = 0; i < length; i += 1) {
    const char = LETTERS[rng.int(LETTERS.length)]
    result += rng.int(8) === 0 ? " " : char
  }
  return result
}

const generateSteps = (count: number, rng: XorShift32) => {
  const steps: SpectatorStep[] = []
  let virtualLength = 0
  let maxLength = 0
  let typedChars = 0
  let deleteOps = 0
  let overDeleteAttempts = 0

  const removeChars = (amount: number) => {
    if (virtualLength <= 0) {
      overDeleteAttempts += 1
      virtualLength = 0
      return
    }
    const removal = Math.min(amount, virtualLength)
    virtualLength -= removal
    deleteOps += removal
  }

  for (let i = 0; i < count; i += 1) {
    const roll = rng.next()
    if (roll < 0.55) {
      const chars = Math.max(1, rng.int(6))
      const text = randomText(chars, rng)
      steps.push({ action: "type", text, typingDelayMs: rng.int(10) })
      typedChars += text.length
      virtualLength += text.length
      maxLength = Math.max(maxLength, virtualLength)
      continue
    }
    if (roll < 0.85) {
      const key = KEY_POOL[rng.int(KEY_POOL.length)]
      const repeat = rng.int(3) === 0 ? 2 : 1
      steps.push({ action: "press", key, repeat })
      switch (key) {
        case "backspace":
        case "delete":
          removeChars(repeat)
          break;
        case "ctrl+backspace":
        case "ctrl+delete":
          removeChars(3 * repeat)
          break;
        default:
          break;
      }
      continue
    }
    steps.push({ action: "wait", ms: 50 + rng.int(200) })
  }
  steps.push({ action: "snapshot", label: "after-fuzz" })
  return {
    steps,
    stats: {
      finalLength: virtualLength,
      maxLength,
      typedChars,
      deleteOps,
      overDeleteAttempts,
    },
  }
}

const computeAggregate = (stats: IterationStats[]) => {
  if (stats.length === 0) return null
  const maxLength = Math.max(...stats.map((entry) => entry.maxLength))
  const maxTypedChars = Math.max(...stats.map((entry) => entry.typedChars))
  const maxDeleteOps = Math.max(...stats.map((entry) => entry.deleteOps))
  const overDeleteAttempts = stats.reduce((sum, entry) => sum + entry.overDeleteAttempts, 0)
  const totalTypedChars = stats.reduce((sum, entry) => sum + entry.typedChars, 0)
  const totalDeleteOps = stats.reduce((sum, entry) => sum + entry.deleteOps, 0)
  const finalLengths = stats.map((entry) => entry.finalLength)
  return {
    iterations: stats.length,
    maxLength,
    maxTypedChars,
    maxDeleteOps,
    overDeleteAttempts,
    totalTypedChars,
    totalDeleteOps,
    finalLengthRange: {
      min: Math.min(...finalLengths),
      max: Math.max(...finalLengths),
    },
  }
}

const ensureDistBuild = async () => {
  const distPath = path.resolve(__dirname, "../dist/main.js")
  try {
    await (await import("node:fs/promises")).access(distPath)
  } catch {
    throw new Error("dist/main.js not found. Run `npm run build` before fuzzing.")
  }
}

const formatTimestamp = () => {
  const now = new Date()
  const pad = (value: number) => value.toString().padStart(2, "0")
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("")
}

const main = async () => {
  await ensureDistBuild()
  const options = parseArgs()
  const rng = new XorShift32(options.seed)
  const runDir = path.join(options.artifactDir, formatTimestamp())
  await fs.mkdir(runDir, { recursive: true })
  const runPath = path.join(runDir, "run.json")
  const baseRunInfo = {
    seed: options.seed,
    requestedIterations: options.iterations,
    stepsPerIteration: options.stepsPerIteration,
    config: options.configPath,
    baseUrl: options.baseUrl,
    cols: options.cols,
    rows: options.rows,
    startedAt: Date.now(),
  }
  await fs.writeFile(runPath, JSON.stringify(baseRunInfo, null, 2), "utf8")
  const iterationStats: IterationStats[] = []
  const writeRunSummary = async () => {
    const aggregate = computeAggregate(iterationStats)
    const payload: Record<string, unknown> = { ...baseRunInfo }
    if (iterationStats.length > 0) {
      payload.iterationStats = iterationStats
    }
    if (aggregate) {
      payload.aggregate = aggregate
    }
    await fs.writeFile(runPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8")
  }
  console.log(
    `[key-fuzz] iterations=${options.iterations} steps=${options.stepsPerIteration} seed=${options.seed} baseUrl=${options.baseUrl}`,
  )

  for (let i = 0; i < options.iterations; i += 1) {
    const { steps, stats } = generateSteps(options.stepsPerIteration, rng)
    try {
      await runSpectatorHarness({
        steps,
        command: options.command,
        configPath: options.configPath,
        baseUrl: options.baseUrl,
        cols: options.cols,
        rows: options.rows,
        echo: false,
        submitTimeoutMs: options.submitTimeoutMs,
      })
      iterationStats.push({ iteration: i + 1, ...stats })
      await writeRunSummary()
      console.log(`[key-fuzz] iteration ${i + 1}/${options.iterations} ok (maxLength=${stats.maxLength}, typed=${stats.typedChars}, deletes=${stats.deleteOps})`)
    } catch (error) {
      console.error(`[key-fuzz] iteration ${i + 1} failed: ${(error as Error).message}`)
      const failureDir = path.join(runDir, `failure-${String(i + 1).padStart(3, "0")}`)
      await fs.mkdir(failureDir, { recursive: true })
      await fs.writeFile(
        path.join(failureDir, "steps.json"),
        JSON.stringify({ seed: options.seed, iteration: i + 1, steps }, null, 2),
        "utf8",
      )
      await fs.writeFile(path.join(failureDir, "error.txt"), (error as Error).stack ?? String(error), "utf8")
      await writeRunSummary()
      console.error(`[key-fuzz] failure artifacts -> ${failureDir}`)
      process.exitCode = 1
      return
    }
  }
  await writeRunSummary()
  const aggregate = computeAggregate(iterationStats)
  if (aggregate) {
    console.log(
      `[key-fuzz] Completed ${options.iterations} iterations (maxLength=${aggregate.maxLength}, totalDeletes=${aggregate.totalDeleteOps}).`,
    )
  } else {
    console.log(`[key-fuzz] Completed ${options.iterations} iterations without harness errors.`)
  }
}

main().catch((error) => {
  console.error("key-fuzz failed:", error)
  process.exitCode = 1
})
