import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { ensureDir, writeJson, writeText } from "./reports/artifactBundle"

type FuzzProfile = "mixed" | "resize-markdown" | "modal" | "tool" | "lifecycle"

interface Options {
  readonly seeds: number[]
  readonly profile: FuzzProfile
  readonly outDir: string
  readonly batchPath?: string
  readonly campaign: string
}

const usage = `Usage: pnpm scenario:fuzz [options]

Options:
  --seed <n>              Generate one seed. May be repeated.
  --seed=<n>              Generate one seed.
  --seeds <a,b,c>         Generate comma-separated seeds.
  --seeds=a,b,c           Generate comma-separated seeds.
  --seed-start <n>        First seed for a range. Default: 17.
  --count <n>             Number of seeds for range. Default: 1.
  --profile <name>        mixed | resize-markdown | modal | tool | lifecycle. Default: mixed.
  --out-dir <path>        Scenario output dir. Default: scenarios/v6/fuzz/minimized.
  --batch <path>          Batch manifest path. Default: scenarios/v6/batches/fuzz_<profile>_<first>_<count>.json.
  --campaign <name>       Campaign name. Default: p14-v6-fuzz-minimization.
  --help                  Print this help without writing files.
`

const parseNumber = (value: string | undefined, label: string): number => {
  const parsed = Number.parseInt(String(value ?? ""), 10)
  if (!Number.isFinite(parsed)) throw new Error(`${label} must be a number`)
  return parsed
}

const parseProfile = (value: string | undefined): FuzzProfile => {
  const normalized = String(value ?? "mixed").trim()
  if (["mixed", "resize-markdown", "modal", "tool", "lifecycle"].includes(normalized)) return normalized as FuzzProfile
  throw new Error(`unsupported profile: ${normalized}`)
}

const parseSeedList = (value: string | undefined): number[] =>
  String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => parseNumber(item, "seed"))

const parseArgs = (argv: string[]): Options | "help" => {
  const explicitSeeds: number[] = []
  let seedStart = 17
  let count = 1
  let profile: FuzzProfile = "mixed"
  let outDir = "scenarios/v6/fuzz/minimized"
  let batchPath: string | undefined
  let campaign = "p14-v6-fuzz-minimization"

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === "--help" || arg === "-h") return "help"
    if (arg === "--seed") explicitSeeds.push(parseNumber(argv[++i], "seed"))
    else if (arg.startsWith("--seed=")) explicitSeeds.push(parseNumber(arg.split("=")[1], "seed"))
    else if (arg === "--seeds") explicitSeeds.push(...parseSeedList(argv[++i]))
    else if (arg.startsWith("--seeds=")) explicitSeeds.push(...parseSeedList(arg.split("=")[1]))
    else if (arg === "--seed-start") seedStart = parseNumber(argv[++i], "seed-start")
    else if (arg.startsWith("--seed-start=")) seedStart = parseNumber(arg.split("=")[1], "seed-start")
    else if (arg === "--count") count = parseNumber(argv[++i], "count")
    else if (arg.startsWith("--count=")) count = parseNumber(arg.split("=")[1], "count")
    else if (arg === "--profile") profile = parseProfile(argv[++i])
    else if (arg.startsWith("--profile=")) profile = parseProfile(arg.split("=")[1])
    else if (arg === "--out-dir") outDir = String(argv[++i] ?? "")
    else if (arg.startsWith("--out-dir=")) outDir = String(arg.split("=")[1] ?? "")
    else if (arg === "--batch") batchPath = String(argv[++i] ?? "")
    else if (arg.startsWith("--batch=")) batchPath = String(arg.split("=")[1] ?? "")
    else if (arg === "--campaign") campaign = String(argv[++i] ?? "")
    else if (arg.startsWith("--campaign=")) campaign = String(arg.split("=")[1] ?? "")
    else throw new Error(`unknown argument: ${arg}`)
  }

  if (count < 1 || count > 500) throw new Error("count must be between 1 and 500")
  const seeds = explicitSeeds.length > 0 ? [...new Set(explicitSeeds)] : Array.from({ length: count }, (_, index) => seedStart + index)
  return { seeds, profile, outDir, batchPath, campaign }
}

const markdownFixtureFor = (seed: number) => (["mixed-mdx", "table-rowwise", "code-fence", "long-list"] as const)[Math.abs(seed) % 4]
const chunkModeFor = (seed: number) => (["random", "syntax", "char"] as const)[Math.abs(seed) % 3]
const modalFor = (seed: number) => (["slash", "model-picker", "transcript", "shortcuts"] as const)[Math.abs(seed) % 4]

const dimensionsFor = (seed: number) => {
  const cols = 54 + (Math.abs(seed * 17) % 78)
  const rows = 14 + (Math.abs(seed * 11) % 22)
  return { cols, rows }
}

const resizeStepsFor = (seed: number, cols: number, rows: number) => {
  const shrink = 10 + (Math.abs(seed) % 18)
  return [
    { kind: "resize", cols: Math.max(50, cols - shrink), rows: Math.max(12, rows - 3), settleMs: 60 },
    { kind: "resize", cols: Math.max(48, cols - Math.floor(shrink / 2)), rows: Math.max(12, rows - 1), settleMs: 40 },
    { kind: "resize", cols, rows, settleMs: 80 },
  ]
}

const toolStepsFor = (seed: number, id: string) => {
  const toolId = `${id}_tool_${seed}`
  const lineCount = 4 + (Math.abs(seed) % 10)
  const output = Array.from({ length: lineCount }, (_, idx) => `fuzz ${seed} tool line ${idx + 1}`).join("\n") + "\n"
  return [
    { kind: "tool", event: { kind: "start", toolId, name: "shell_command", args: { command: `printf fuzz-${seed}` } } },
    { kind: "tool", event: { kind: "stdout", toolId, text: output, stream: seed % 2 === 0 } },
    { kind: "tool", event: { kind: "result", toolId, status: "ok", summary: `fuzz tool ${seed} completed`, output: `fuzz-${seed}` } },
  ]
}

const scenarioFor = (seed: number, profile: FuzzProfile, campaign: string) => {
  const id = `v6_fuzz_${profile.replace(/[^a-z0-9]+/g, "_")}_${seed}`
  const { cols, rows } = dimensionsFor(seed)
  const response = `V6 fuzz seed ${seed} profile ${profile} complete.`
  const timeline: any[] = []

  if (profile === "modal" || (profile === "mixed" && seed % 5 === 0)) {
    timeline.push({ kind: "open", surface: modalFor(seed) })
    timeline.push({ kind: "wait", ms: 160 })
    timeline.push({ kind: "key", key: "escape" })
    timeline.push({ kind: "wait", ms: 120 })
  }

  timeline.push({ kind: "submit", text: `Run V6 fuzz seed ${seed} with ${profile} stress.` })
  timeline.push({ kind: "assistant", event: { kind: "start", streamId: "s1" } })

  if (profile === "tool" || profile === "mixed") timeline.push(...toolStepsFor(seed, id))

  timeline.push({ kind: "assistant", event: { kind: "markdown-fixture", streamId: "s1", fixture: markdownFixtureFor(seed), chunking: { mode: chunkModeFor(seed), seed } } })

  if (profile === "resize-markdown" || profile === "mixed") timeline.push(...resizeStepsFor(seed, cols, rows))

  if (profile === "lifecycle") {
    timeline.push({ kind: "fault", event: { kind: "disconnect", reason: `fuzz-disconnect-${seed}` } })
    timeline.push({ kind: "wait", ms: 120 })
  }

  timeline.push({ kind: "assistant", event: { kind: "delta", streamId: "s1", text: response } })
  timeline.push({ kind: "assistant", event: { kind: "complete", streamId: "s1" } })
  timeline.push({ kind: "waitFor", target: { text: response }, timeoutMs: 60000 })
  if (profile !== "resize-markdown" && profile !== "mixed") timeline.push(...resizeStepsFor(seed, cols, rows).slice(0, 2))
  timeline.push({ kind: "checkpoint", id: "fuzz-final" })

  const invariants = [
    { id: "GLOBAL-HOST-HISTORY-PRESERVED", severity: "blocker" },
    { id: "SCROLL-NO-DESTRUCTIVE-CLEAR", severity: "blocker" },
    { id: "SCROLL-PROMPT-CARDINALITY", severity: "blocker" },
    { id: "SCROLL-ASSISTANT-CARDINALITY", severity: "blocker" },
    { id: "SCROLL-NO-DUPLICATE-ASSISTANT-TAIL", severity: "blocker" },
    { id: "SCROLL-NO-COMPOSER-PROMPT-REPLAY", severity: "blocker" },
    { id: "GLOBAL-COMPOSER-VISIBLE", severity: "blocker" },
    { id: "GLOBAL-NO-RAW-ANSI", severity: "blocker" },
    { id: "RESIZE-ACTION-OBSERVED", severity: "blocker" },
    { id: "RESIZE-COMPOSER-ALWAYS-VISIBLE", severity: "blocker" },
    { id: "MD-FINAL-TEXT-COMPLETE", severity: "blocker" },
  ]
  if (profile === "tool" || profile === "mixed") invariants.push({ id: "TOOL-START-VISIBLE", severity: "blocker" }, { id: "TOOL-RESULT-SINGULAR", severity: "blocker" })
  if (profile === "modal" || (profile === "mixed" && seed % 5 === 0)) invariants.push({ id: "MODAL-OPEN-CONFIRMED", severity: "blocker" }, { id: "MODAL-FOCUS-RETURNS-TO-COMPOSER", severity: "blocker" })
  if (profile === "lifecycle") {
    invariants.push(
      { id: "SCROLL-NO-CONTROL-CHROME-BODY", severity: "blocker" },
      { id: "MD-NO-STREAMING-PARTIAL-REPLAY", severity: "blocker" },
      { id: "SCROLL-NO-INPUT-SEPARATOR-REPLAY", severity: "blocker" },
    )
  }

  return {
    schemaVersion: 1,
    id,
    title: `V6 fuzz seed ${seed} (${profile})`,
    family: "fuzz",
    campaign,
    claimScope: "fuzz",
    productionEquivalence: {
      required: true,
      claimTier: "S2",
      allowedSyntheticLayers: ["model", "tool", "fault", "time", "workspace"],
      forbiddenBypass: ["renderer", "transcript-store", "composer", "scrollback-feed"],
    },
    environment: {
      dummyWorkspace: { gitInit: true, files: [{ path: "README.md", content: `# ${id}\n` }] },
      prelaunchShellHistory: [{ id: "fuzz-pre", text: `BB_V6_FUZZ_PRE_${profile.toUpperCase()}_${seed}` }],
    },
    launch: { mode: "classic", engineMode: "test-owned", startupWait: { composerReady: true, timeoutMs: 20000 }, timeoutMs: 180000 },
    terminal: { lanes: ["pty"], initial: { cols, rows }, resizePolicy: "fuzzed", captureGrid: true, captureScrollback: true },
    timeline,
    invariants,
    actionRequirements: { waitTargetsObserved: "required", resizeObserved: "required" },
    performanceBudget: { maxDurationMs: 180000, maxRawBytes: 8000000, maxFrames: 1800, maxStateDumps: 3000 },
    transcriptExpectation: { promptCardinality: "exactly-once", assistantCardinality: "at-least-once", toolCardinality: profile === "tool" || profile === "mixed" ? "at-least-once" : "not-required" },
    mutationProfile: { seed, dimensions: [profile, `cols:${cols}`, `rows:${rows}`, `chunk:${chunkModeFor(seed)}`, `fixture:${markdownFixtureFor(seed)}`] },
    minimization: { enabled: true, preserveSteps: ["launch", "checkpoint:fuzz-final", `seed:${seed}`] },
    artifacts: { required: ["manifest", "raw", "grid", "state", "invariant-report"] },
  }
}

const main = async () => {
  const parsed = parseArgs(process.argv.slice(2))
  if (parsed === "help") {
    process.stdout.write(usage)
    return
  }
  const { seeds, profile, outDir, campaign } = parsed
  const targetDir = path.resolve(process.cwd(), outDir)
  await ensureDir(targetDir)
  const scenarioPaths: string[] = []
  for (const seed of seeds) {
    const scenario = scenarioFor(seed, profile, campaign)
    const target = path.join(targetDir, `${scenario.id}.json`)
    await writeJson(target, scenario)
    await writeText(path.join(targetDir, `${scenario.id}_minimization_report.md`), [
      `# Fuzz Minimization Report`,
      ``,
      `- seed: ${seed}`,
      `- profile: ${profile}`,
      `- status: generated deterministic fuzz candidate; minimization is available by removing timeline steps while preserving invariant failure`,
      `- scenario: ${path.relative(process.cwd(), target)}`,
      ``,
    ].join("\n"))
    scenarioPaths.push(path.relative(process.cwd(), target))
  }

  const batchPath = path.resolve(
    process.cwd(),
    parsed.batchPath ?? `scenarios/v6/batches/fuzz_${profile.replace(/[^a-z0-9]+/g, "_")}_${seeds[0]}_${seeds.length}.json`,
  )
  await ensureDir(path.dirname(batchPath))
  await writeJson(batchPath, {
    id: `v6_fuzz_${profile.replace(/[^a-z0-9]+/g, "_")}_${seeds[0]}_${seeds.length}`,
    campaign,
    scenarios: scenarioPaths,
  })
  console.log(`[scenario:fuzz] wrote ${scenarioPaths.length} scenario(s) to ${path.relative(process.cwd(), targetDir)}`)
  console.log(`[scenario:fuzz] wrote batch ${path.relative(process.cwd(), batchPath)}`)
}

await main()
