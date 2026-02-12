import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import { evaluateJitterGate } from "../src/repl/markdown/jitterGate.js"

interface Args {
  baseline: string
  candidate: string
  strict: boolean
  out: string | null
  thresholdsPath: string | null
}

const parseArgs = (): Args => {
  let baseline = ""
  let candidate = ""
  let strict = false
  let out: string | null = null
  let thresholdsPath: string | null = null
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--baseline") baseline = argv[++i] ?? ""
    else if (arg === "--candidate") candidate = argv[++i] ?? ""
    else if (arg === "--strict") strict = true
    else if (arg === "--out") out = argv[++i] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++i] ?? null
  }
  if (!baseline || !candidate) {
    throw new Error("Usage: runtime_jitter_matrix.ts --baseline <json> --candidate <json> [--strict]")
  }
  return { baseline, candidate, strict, out, thresholdsPath }
}

const loadJitterThresholds = (customPath: string | null): { maxPrefixChurnDelta: number; maxReflowDelta: number } => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    return {
      maxPrefixChurnDelta: Number.isFinite(Number(parsed?.jitter?.maxPrefixChurnDelta))
        ? Number(parsed.jitter.maxPrefixChurnDelta)
        : 0,
      maxReflowDelta: Number.isFinite(Number(parsed?.jitter?.maxReflowDelta))
        ? Number(parsed.jitter.maxReflowDelta)
        : 0,
    }
  } catch {
    return { maxPrefixChurnDelta: 0, maxReflowDelta: 0 }
  }
}

const normalizeScenarioMap = (rawPath: string): Record<string, string[]> => {
  const parsed = JSON.parse(readFileSync(path.resolve(rawPath), "utf8")) as any
  const obj = parsed?.scenarios && typeof parsed.scenarios === "object" ? parsed.scenarios : parsed
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
    throw new Error(`Expected scenario map object in ${rawPath}`)
  }
  const out: Record<string, string[]> = {}
  for (const [key, value] of Object.entries(obj)) {
    if (Array.isArray(value)) {
      out[key] = value.map((item) => String(item ?? ""))
      continue
    }
    if (value && typeof value === "object" && Array.isArray((value as any).frames)) {
      out[key] = (value as any).frames.map((item: unknown) => String(item ?? ""))
      continue
    }
    throw new Error(`Scenario "${key}" is not an array of frames`)
  }
  return out
}

const main = (): void => {
  const args = parseArgs()
  const thresholds = loadJitterThresholds(args.thresholdsPath)
  const baseline = normalizeScenarioMap(args.baseline)
  const candidate = normalizeScenarioMap(args.candidate)
  const scenarioNames = Array.from(new Set([...Object.keys(baseline), ...Object.keys(candidate)])).sort()
  const results = scenarioNames.map((name) => {
    const baselineFrames = baseline[name] ?? []
    const candidateFrames = candidate[name] ?? []
    const missing = baseline[name] == null || candidate[name] == null
    const gate = evaluateJitterGate(baselineFrames, candidateFrames, thresholds)
    return {
      scenario: name,
      missing,
      ...gate,
      ok: !missing && gate.ok,
    }
  })
  const failCount = results.filter((entry) => !entry.ok).length
  const summary = {
    ok: failCount === 0,
    total: results.length,
    failCount,
    thresholds,
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
}

main()
