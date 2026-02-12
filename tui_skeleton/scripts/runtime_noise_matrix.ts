import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { evaluateTranscriptNoiseGate } from "../src/commands/repl/transcriptNoiseGate.js"

type RawEvent = Record<string, unknown>

interface Args {
  fixtures: string[]
  strict: boolean
  threshold: number | null
  out: string | null
  thresholdsPath: string | null
}

const parseArgs = (): Args => {
  let fixtures: string[] = []
  let strict = false
  let threshold: number | null = null
  let out: string | null = null
  let thresholdsPath: string | null = null
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--fixtures") {
      fixtures = (argv[++i] ?? "")
        .split(",")
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
    } else if (arg === "--strict") strict = true
    else if (arg === "--threshold") threshold = Number(argv[++i] ?? "0.8")
    else if (arg === "--out") out = argv[++i] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++i] ?? null
  }
  if (fixtures.length === 0) {
    throw new Error("Usage: runtime_noise_matrix.ts --fixtures <a.jsonl,b.jsonl,...> [--strict]")
  }
  return { fixtures, strict, threshold, out, thresholdsPath }
}

const parseEvent = (raw: string): RawEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return parsed.event as RawEvent
    if (typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return inner as RawEvent
      } catch {
        return null
      }
    }
    if (parsed.type) return parsed as RawEvent
  }
  return null
}

const loadNoiseThreshold = (customPath: string | null): number => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    const value = Number(parsed?.noise?.threshold)
    return Number.isFinite(value) ? value : 0.8
  } catch {
    return 0.8
  }
}

const stateFromFixture = (fixturePath: string): any => {
  const controller = new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
  }
  const lines = readFileSync(path.resolve(fixturePath), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    controller.applyEvent(event)
  }
  return controller.getState()
}

const main = (): void => {
  const args = parseArgs()
  const threshold = args.threshold ?? loadNoiseThreshold(args.thresholdsPath)
  const results = args.fixtures.map((fixturePath) => {
    const gate = evaluateTranscriptNoiseGate(stateFromFixture(fixturePath), threshold)
    return {
      fixture: fixturePath,
      ...gate,
    }
  })
  const failCount = results.filter((entry) => !entry.ok).length
  const summary = {
    ok: failCount === 0,
    threshold,
    total: results.length,
    failCount,
    results,
  }
  const output = JSON.stringify(summary, null, 2)
  console.log(output)
  if (args.out) {
    writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  }
  if (args.strict && failCount > 0) {
    process.exit(1)
  }
  process.exit(0)
}

main()
