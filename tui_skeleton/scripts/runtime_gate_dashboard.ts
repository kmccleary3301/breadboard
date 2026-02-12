import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

interface Args {
  artifactsDir: string
  out: string | null
  markdownOut: string | null
  strict: boolean
  strictTestsOk: boolean
  thresholdsPath: string | null
}

interface GateRow {
  key: string
  label: string
  ok: boolean
  source: string
  threshold: string
  observed: string
  summary: string
}

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let artifactsDir = path.resolve("../artifacts/runtime_gates")
  let out: string | null = null
  let markdownOut: string | null = null
  let strict = false
  let strictTestsOk = false
  let thresholdsPath: string | null = null
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--artifacts") artifactsDir = path.resolve(argv[++i] ?? artifactsDir)
    else if (arg === "--out") out = path.resolve(argv[++i] ?? "")
    else if (arg === "--markdown-out") markdownOut = path.resolve(argv[++i] ?? "")
    else if (arg === "--strict") strict = true
    else if (arg === "--strict-tests-ok") strictTestsOk = true
    else if (arg === "--thresholds") thresholdsPath = argv[++i] ?? null
  }
  return { artifactsDir, out, markdownOut, strict, strictTestsOk, thresholdsPath }
}

const loadJson = (filePath: string): any | null => {
  try {
    return JSON.parse(readFileSync(filePath, "utf8"))
  } catch {
    return null
  }
}

const loadThresholds = (customPath: string | null): { jitterPrefix: number; jitterReflow: number; noise: number } => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  const parsed = loadJson(target)
  return {
    jitterPrefix: Number.isFinite(Number(parsed?.jitter?.maxPrefixChurnDelta))
      ? Number(parsed?.jitter?.maxPrefixChurnDelta)
      : 0,
    jitterReflow: Number.isFinite(Number(parsed?.jitter?.maxReflowDelta))
      ? Number(parsed?.jitter?.maxReflowDelta)
      : 0,
    noise: Number.isFinite(Number(parsed?.noise?.threshold)) ? Number(parsed?.noise?.threshold) : 0.8,
  }
}

const toPassFail = (ok: boolean): string => (ok ? "PASS" : "FAIL")

const renderMarkdown = (rows: GateRow[], overallOk: boolean): string => {
  const lines = [
    "### Runtime Gate Dashboard",
    "",
    `Overall: **${toPassFail(overallOk)}**`,
    "",
    "| Gate | Status | Source | Threshold | Observed | Summary |",
    "| --- | --- | --- | --- | --- | --- |",
    ...rows.map((row) =>
      `| ${row.label} | ${toPassFail(row.ok)} | ${row.source} | ${row.threshold} | ${row.observed} | ${row.summary} |`,
    ),
    "",
  ]
  return lines.join("\n")
}

const main = (): void => {
  const args = parseArgs()
  const thresholds = loadThresholds(args.thresholdsPath)
  const jitterGate = loadJson(path.join(args.artifactsDir, "jitter_gate.json"))
  const noiseGate = loadJson(path.join(args.artifactsDir, "noise_gate.json"))
  const noiseMatrix = loadJson(path.join(args.artifactsDir, "noise_matrix.json"))

  const rows: GateRow[] = []
  const strictStatus = args.strictTestsOk

  rows.push({
    key: "transition_legality",
    label: "Transition legality/stuck state",
    ok: strictStatus,
    source: "runtime:gates:strict",
    threshold: "0 illegal, no stuck state",
    observed: strictStatus ? "strict suite passed" : "strict suite status unavailable",
    summary: strictStatus ? "ok" : "missing strict-suite confirmation",
  })
  rows.push({
    key: "reasoning_safety",
    label: "Reasoning safety defaults",
    ok: strictStatus,
    source: "runtime:gates:strict",
    threshold: "default-safe visibility",
    observed: strictStatus ? "strict suite passed" : "strict suite status unavailable",
    summary: strictStatus ? "ok" : "missing strict-suite confirmation",
  })
  rows.push({
    key: "streaming_stress",
    label: "Streaming stress (table/fence)",
    ok: strictStatus,
    source: "runtime:gates:strict",
    threshold: "stress gate pass",
    observed: strictStatus ? "strict suite passed" : "strict suite status unavailable",
    summary: strictStatus ? "ok" : "missing strict-suite confirmation",
  })

  const jitterOk = Boolean(jitterGate?.ok)
  const jitterObservedPrefix = Number(jitterGate?.comparison?.deltaPrefixChurn)
  const jitterObservedReflow = Number(jitterGate?.comparison?.deltaReflowCount)
  rows.push({
    key: "jitter_threshold",
    label: "Jitter threshold",
    ok: jitterOk,
    source: "runtime_jitter_gate.ts",
    threshold: `prefix<=${thresholds.jitterPrefix}, reflow<=${thresholds.jitterReflow}`,
    observed:
      Number.isFinite(jitterObservedPrefix) && Number.isFinite(jitterObservedReflow)
        ? `prefix=${jitterObservedPrefix}, reflow=${jitterObservedReflow}`
        : "missing",
    summary: String(jitterGate?.summary ?? "missing jitter_gate.json"),
  })

  const noiseOk = Boolean(noiseGate?.ok)
  const noiseObserved = Number(noiseGate?.metrics?.ratio)
  rows.push({
    key: "transcript_noise",
    label: "Transcript noise ratio",
    ok: noiseOk,
    source: "runtime_transcript_noise_gate.ts",
    threshold: `ratio<=${thresholds.noise}`,
    observed: Number.isFinite(noiseObserved) ? `ratio=${noiseObserved.toFixed(3)}` : "missing",
    summary: String(noiseGate?.summary ?? "missing noise_gate.json"),
  })

  if (noiseMatrix && typeof noiseMatrix === "object") {
    const failCount = Number(noiseMatrix.failCount)
    rows.push({
      key: "noise_matrix",
      label: "Noise matrix sweep",
      ok: Boolean(noiseMatrix.ok),
      source: "runtime_noise_matrix.ts",
      threshold: `fail_count=0`,
      observed: Number.isFinite(failCount) ? `fail_count=${failCount}` : "missing",
      summary: String(noiseMatrix.ok ? "ok" : "failed"),
    })
  }

  const overallOk = rows.every((row) => row.ok)
  const summary = {
    ok: overallOk,
    generatedAt: new Date().toISOString(),
    artifactsDir: args.artifactsDir,
    rows,
  }
  const markdown = renderMarkdown(rows, overallOk)
  if (args.out) {
    writeFileSync(args.out, `${JSON.stringify(summary, null, 2)}\n`, "utf8")
  }
  if (args.markdownOut) {
    writeFileSync(args.markdownOut, `${markdown}\n`, "utf8")
  }
  console.log(markdown)
  if (args.strict && !overallOk) {
    process.exit(1)
  }
  process.exit(0)
}

main()
