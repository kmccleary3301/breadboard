import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { evaluateJitterGate, renderJitterGateJson } from "../src/repl/markdown/jitterGate.js"

interface Args {
  baseline: string
  candidate: string
  strict: boolean
  maxPrefixDelta: number | null
  maxReflowDelta: number | null
  out: string | null
  thresholdsPath: string | null
}

const parseArgs = (): Args => {
  let baseline = ""
  let candidate = ""
  let strict = false
  let maxPrefixDelta: number | null = null
  let maxReflowDelta: number | null = null
  let out: string | null = null
  let thresholdsPath: string | null = null
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--baseline") baseline = argv[++i] ?? ""
    else if (arg === "--candidate") candidate = argv[++i] ?? ""
    else if (arg === "--strict") strict = true
    else if (arg === "--max-prefix-delta") maxPrefixDelta = Number(argv[++i] ?? "0")
    else if (arg === "--max-reflow-delta") maxReflowDelta = Number(argv[++i] ?? "0")
    else if (arg === "--out") out = argv[++i] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++i] ?? null
  }
  if (!baseline || !candidate) {
    throw new Error("Usage: runtime_jitter_gate.ts --baseline <json> --candidate <json> [--strict]")
  }
  return { baseline, candidate, strict, maxPrefixDelta, maxReflowDelta, out, thresholdsPath }
}

const loadThresholds = (customPath: string | null): { maxPrefixChurnDelta: number; maxReflowDelta: number } => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    const jitter = parsed?.jitter ?? {}
    const maxPrefixChurnDelta = Number(jitter.maxPrefixChurnDelta)
    const maxReflowDelta = Number(jitter.maxReflowDelta)
    return {
      maxPrefixChurnDelta: Number.isFinite(maxPrefixChurnDelta) ? maxPrefixChurnDelta : 0,
      maxReflowDelta: Number.isFinite(maxReflowDelta) ? maxReflowDelta : 0,
    }
  } catch {
    return { maxPrefixChurnDelta: 0, maxReflowDelta: 0 }
  }
}

const parseFrames = (filePath: string): string[] => {
  const raw = readFileSync(filePath, "utf8")
  const parsed = JSON.parse(raw)
  if (Array.isArray(parsed)) return parsed.map((item) => String(item ?? ""))
  if (parsed && typeof parsed === "object" && Array.isArray((parsed as any).frames)) {
    return (parsed as any).frames.map((item: unknown) => String(item ?? ""))
  }
  throw new Error(`Expected JSON array or { frames: [] } in ${filePath}`)
}

const main = (): void => {
  const args = parseArgs()
  const defaults = loadThresholds(args.thresholdsPath)
  const baselineFrames = parseFrames(args.baseline)
  const candidateFrames = parseFrames(args.candidate)
  const result = evaluateJitterGate(baselineFrames, candidateFrames, {
    maxPrefixChurnDelta: args.maxPrefixDelta ?? defaults.maxPrefixChurnDelta,
    maxReflowDelta: args.maxReflowDelta ?? defaults.maxReflowDelta,
  })
  const output = renderJitterGateJson(result)
  console.log(output)
  if (args.out) {
    writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  }
  if (args.strict && !result.ok) {
    process.exit(1)
  }
}

main()
