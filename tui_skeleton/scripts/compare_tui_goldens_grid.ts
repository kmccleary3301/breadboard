import { promises as fs } from "node:fs"
import path from "node:path"
import yaml from "yaml"
import { computeGridDiff } from "../tools/tty/gridDiff.js"
import { maskGridLines } from "./mask_grid.js"

type ScenarioConfig = {
  id: string
  normalize?: NormalizeOptions
  style?: StyleOptions
}

type Manifest = {
  version: number
  defaults?: {
    normalize?: NormalizeOptions
    style?: StyleOptions
  }
  scenarios: ScenarioConfig[]
}

type NormalizeOptions = {
  timestamps?: boolean
  tokens?: boolean
  paths?: boolean
  status?: boolean
}

type StyleOptions = {
  require_ansi?: boolean
  diff_add?: boolean
  diff_del?: boolean
  diff_add_bg?: boolean
  diff_del_bg?: boolean
  tool_dot?: boolean
  collapse_dim?: boolean
}

type CliOptions = {
  manifestPath: string
  candidateRoot: string
  blessedRoot: string
  diffRoot: string
  strict: boolean
  summary: boolean
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let manifestPath = "misc/tui_goldens/manifests/tui_goldens.yaml"
  let candidateRoot = ""
  let blessedRoot = "misc/tui_goldens/scenarios"
  let diffRoot = ""
  let strict = false
  let summary = false

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--manifest":
        manifestPath = args[++i] ?? manifestPath
        break
      case "--candidate":
        candidateRoot = args[++i] ?? ""
        break
      case "--blessed-root":
        blessedRoot = args[++i] ?? blessedRoot
        break
      case "--diff-root":
        diffRoot = args[++i] ?? diffRoot
        break
      case "--strict":
        strict = true
        break
      case "--summary":
        summary = true
        break
      default:
        break
    }
  }

  if (!candidateRoot) {
    throw new Error("Usage: tsx scripts/compare_tui_goldens_grid.ts --candidate <dir> [--manifest <path>]")
  }

  return { manifestPath, candidateRoot, blessedRoot, diffRoot, strict, summary }
}

const normalizePath = (value: string, baseDir: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(baseDir, value)
}

const readManifest = async (manifestPath: string): Promise<Manifest> => {
  const raw = await fs.readFile(manifestPath, "utf8")
  const parsed = yaml.parse(raw) as Manifest
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.scenarios)) {
    throw new Error(`Invalid manifest: ${manifestPath}`)
  }
  return parsed
}

const stripAnsi = (value: string): string => value.replace(/\u001b\[[0-9;]*m/g, "")

const applyNormalize = (value: string, options: NormalizeOptions): string => {
  let out = value
  if (options.paths) {
    out = out
      .replace(/\/shared_folders\/\S+/g, "<path>")
      .replace(/\/home\/\S+/g, "<path>")
      .replace(/\/Users\/\S+/g, "<path>")
      .replace(/[A-Za-z]:\\\\\S+/g, "<path>")
      .replace(/~\/\S+/g, "<path>")
  }
  if (options.tokens) {
    out = out.replace(/(tokens?:\s*)(\d[\d,]*)/gi, "$1<tokens>")
    out = out.replace(/\b(\d[\d,]*)\s+tokens?\b/gi, "<tokens> tokens")
  }
  if (options.timestamps) {
    out = out.replace(/Cooked for [0-9hms\s.]+/gi, "Cooked for <elapsed>")
    out = out.replace(/Cooking [0-9hms\s.]+/gi, "Cooking <elapsed>")
  }
  if (options.status) {
    if (/(Deciphering|Spelunking|Reconnecting|Disconnected|Working)/i.test(out)) {
      out = "<status>"
    }
  }
  return out
}

const normalizeText = (value: string, options: NormalizeOptions): string =>
  value
    .split(/\r?\n/)
    .map((line) => applyNormalize(line, options))
    .join("\n")

const evaluateStyleAssertions = (rawText: string, style: StyleOptions): string[] => {
  const failures: string[] = []
  if (!style || Object.keys(style).length === 0) return failures

  const lines = rawText.split(/\r?\n/)
  let addAnsi = false
  let delAnsi = false
  let addBg = false
  let delBg = false
  let toolDotLine = false
  let collapseDim = false

  for (const line of lines) {
    const plain = stripAnsi(line)
    const hasAnsi = line !== plain
    const isAdd = !plain.includes("+++") && /\+\s/.test(plain)
    const isDel = !plain.includes("---") && /-\s/.test(plain)
    if (isAdd && hasAnsi) addAnsi = true
    if (isDel && hasAnsi) delAnsi = true
    if (isAdd && /\u001b\[48;/.test(line)) addBg = true
    if (isDel && /\u001b\[48;/.test(line)) delBg = true
    if (plain.startsWith("● ") && (plain.includes("(") || plain.includes("Tool"))) {
      toolDotLine = true
    }
    if ((/lines hidden/.test(plain) || /entries hidden/.test(plain)) && (/\u001b\[2m/.test(line) || plain.length > 0)) {
      collapseDim = true
    }
  }

  if (style.diff_add && !addAnsi && !lines.some((line) => /\+\s/.test(stripAnsi(line)))) failures.push("style_add_missing")
  if (style.diff_del && !delAnsi && !lines.some((line) => /-\s/.test(stripAnsi(line)))) failures.push("style_del_missing")
  if (style.diff_add_bg && !addBg && !lines.some((line) => /\+\s/.test(stripAnsi(line)))) failures.push("style_add_bg_missing")
  if (style.diff_del_bg && !delBg && !lines.some((line) => /-\s/.test(stripAnsi(line)))) failures.push("style_del_bg_missing")
  if (style.tool_dot && !toolDotLine) failures.push("style_tool_dot_missing")
  if (style.collapse_dim && !collapseDim) failures.push("style_collapse_dim_missing")
  if (style.require_ansi && !addAnsi && !delAnsi && !lines.some((line) => /\+\s|-\s/.test(stripAnsi(line)))) {
    failures.push("style_ansi_missing")
  }
  return failures
}

type FrameGrid = {
  grid: string[]
  normalGrid?: string[]
  alternateGrid?: string[]
}

const readGrid = async (filePath: string): Promise<FrameGrid | null> => {
  const raw = await fs.readFile(filePath, "utf8").catch(() => "")
  if (!raw) return null
  try {
    return JSON.parse(raw) as FrameGrid
  } catch {
    return null
  }
}

const main = async () => {
  const options = parseArgs()
  const manifestPath = normalizePath(options.manifestPath, process.cwd())
  const manifest = await readManifest(manifestPath)
  const candidateRoot = normalizePath(options.candidateRoot, process.cwd())
  const blessedRoot = normalizePath(options.blessedRoot, process.cwd())
  const diffRoot = normalizePath(options.diffRoot || path.join(candidateRoot, "_diffs"), process.cwd())
  await fs.mkdir(diffRoot, { recursive: true })
  const normalizeDefaults = manifest.defaults?.normalize ?? {}
  const styleDefaults = manifest.defaults?.style ?? {}

  let failed = false
  let okCount = 0
  let failCount = 0
  let missingBlessed = 0
  let missingCandidate = 0
  const index: Array<Record<string, unknown>> = []

  for (const scenario of manifest.scenarios) {
    const candidatePath = path.join(candidateRoot, scenario.id, "frame.grid.json")
    const blessedPath = path.join(blessedRoot, scenario.id, "blessed", "frame.grid.json")
    const candidate = await readGrid(candidatePath)
    const blessed = await readGrid(blessedPath)
    const normalizeOptions = { ...normalizeDefaults, ...(scenario.normalize ?? {}) }
    const styleOptions = { ...styleDefaults, ...(scenario.style ?? {}) }
    if (!blessed) {
      console.log(`[grid] ${scenario.id}: missing blessed grid`)
      index.push({ id: scenario.id, ok: false, reason: "missing_blessed" })
      failed = true
      failCount += 1
      missingBlessed += 1
      continue
    }
    if (!candidate) {
      console.log(`[grid] ${scenario.id}: missing candidate grid`)
      index.push({ id: scenario.id, ok: false, reason: "missing_candidate" })
      failed = true
      failCount += 1
      missingCandidate += 1
      continue
    }
    const leftLines = maskGridLines(blessed.grid ?? [], {
      timestamps: true,
      tokens: true,
      paths: true,
      status: true,
      spinner: true,
    })
    const rightLines = maskGridLines(candidate.grid ?? [], {
      timestamps: true,
      tokens: true,
      paths: true,
      status: true,
      spinner: true,
    })
    const diff = computeGridDiff(leftLines, rightLines, { labelA: "blessed", labelB: "candidate" })
    if (!diff.areEqual) {
      console.log(`[grid] ${scenario.id}: mismatch`)
      const diffFile = path.join(diffRoot, `${scenario.id}.grid.diff.txt`)
      await fs.writeFile(diffFile, diff.report, "utf8")
      index.push({ id: scenario.id, ok: false, diff: diffFile })
      failed = true
      failCount += 1
      continue
    }
    if (Object.keys(styleOptions).length > 0) {
      const renderCandidatePath = path.join(candidateRoot, scenario.id, "render.txt")
      const renderRaw = await fs.readFile(renderCandidatePath, "utf8").catch(() => "")
      if (!renderRaw) {
        console.log(`[grid] ${scenario.id}: missing candidate render for style checks`)
        index.push({ id: scenario.id, ok: false, reason: "missing_candidate_render" })
        failed = true
        failCount += 1
        continue
      }
      const normalizedForStyle = normalizeText(renderRaw, normalizeOptions)
      const styleFailures = evaluateStyleAssertions(normalizedForStyle, styleOptions)
      if (styleFailures.length > 0) {
        console.log(`[grid] ${scenario.id}: style mismatch (${styleFailures.join(",")})`)
        index.push({ id: scenario.id, ok: false, reason: styleFailures.join(",") })
        failed = true
        failCount += 1
        continue
      }
    }
    console.log(`[grid] ${scenario.id}: ok`)
    index.push({ id: scenario.id, ok: true })
    okCount += 1
  }

  const indexPath = path.join(diffRoot, "grid.index.json")
  await fs.writeFile(
    indexPath,
    `${JSON.stringify({ okCount, failCount, missingBlessed, missingCandidate, index }, null, 2)}\n`,
    "utf8",
  )

  if (options.summary) {
    const total = okCount + failCount
    console.log(
      `[grid] scenarios=${total} ok=${okCount} fail=${failCount} missing_blessed=${missingBlessed} missing_candidate=${missingCandidate}`,
    )
  }

  if (failed && options.strict) {
    process.exitCode = 2
  }
}

main().catch((error) => {
  console.error("[compare_tui_goldens_grid] failed:", error)
  process.exit(1)
})
