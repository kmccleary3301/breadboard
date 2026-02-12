import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { evaluateTranscriptNoiseGate, renderTranscriptNoiseGateJson } from "../src/commands/repl/transcriptNoiseGate.js"

type RawEvent = Record<string, unknown>

interface Args {
  fixture: string
  strict: boolean
  threshold: number | null
  out: string | null
  thresholdsPath: string | null
}

const parseArgs = (): Args => {
  let fixture = ""
  let strict = false
  let threshold: number | null = null
  let out: string | null = null
  let thresholdsPath: string | null = null
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--fixture") fixture = argv[++i] ?? ""
    else if (arg === "--strict") strict = true
    else if (arg === "--threshold") threshold = Number(argv[++i] ?? "0.8")
    else if (arg === "--out") out = argv[++i] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++i] ?? null
  }
  if (!fixture) {
    throw new Error("Usage: runtime_transcript_noise_gate.ts --fixture <events.jsonl> [--strict] [--threshold <n>]")
  }
  return { fixture, strict, threshold, out, thresholdsPath }
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

const main = (): void => {
  const args = parseArgs()
  const fixturePath = path.resolve(args.fixture)
  const defaultThreshold = loadNoiseThreshold(args.thresholdsPath)
  const controller = new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
  }

  const lines = readFileSync(fixturePath, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    controller.applyEvent(event)
  }

  const result = evaluateTranscriptNoiseGate(controller.getState(), args.threshold ?? defaultThreshold)
  const output = renderTranscriptNoiseGateJson(result)
  console.log(output)
  if (args.out) {
    writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  }
  if (args.strict && !result.ok) {
    process.exit(1)
  }
  process.exit(0)
}

main()
