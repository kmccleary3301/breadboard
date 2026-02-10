import { promises as fs } from "node:fs"
import path from "node:path"
import yaml from "yaml"

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
    throw new Error("Usage: tsx scripts/compare_tui_goldens.ts --candidate <dir> [--manifest <path>]")
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
    if (/(Deciphering|Spelunking|Reconnecting|Disconnected)/i.test(out)) {
      out = "<status>"
    }
  }
  return out
}

const normalizeText = (value: string, options: NormalizeOptions): string => {
  const lines = value.split(/\r?\n/)
  return lines.map((line) => applyNormalize(line, options)).join("\n")
}

const diffSample = (expected: string, actual: string, limit = 10) => {
  const expectedLines = expected.split(/\r?\n/)
  const actualLines = actual.split(/\r?\n/)
  const maxLines = Math.max(expectedLines.length, actualLines.length)
  const samples: Array<{ line: number; expected: string; actual: string }> = []
  let mismatches = 0
  for (let i = 0; i < maxLines; i += 1) {
    const exp = expectedLines[i] ?? ""
    const act = actualLines[i] ?? ""
    if (exp !== act) {
      mismatches += 1
      if (samples.length < limit) {
        samples.push({ line: i + 1, expected: exp, actual: act })
      }
    }
  }
  return { mismatches, samples, expectedLines: expectedLines.length, actualLines: actualLines.length }
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
  const index: Array<Record<string, unknown>> = []
  let okCount = 0
  let failCount = 0
  let missingBlessed = 0
  let missingCandidate = 0

  for (const scenario of manifest.scenarios) {
    const candidatePath = path.join(candidateRoot, scenario.id, "render.txt")
    const blessedPath = path.join(blessedRoot, scenario.id, "blessed", "render.txt")
    const candidateRaw = await fs.readFile(candidatePath, "utf8").catch(() => "")
    const blessedRaw = await fs.readFile(blessedPath, "utf8").catch(() => "")
    const normalizeOptions = { ...normalizeDefaults, ...(scenario.normalize ?? {}) }
    const styleOptions = { ...styleDefaults, ...(scenario.style ?? {}) }
    const candidate = normalizeText(candidateRaw, normalizeOptions)
    const blessed = normalizeText(blessedRaw, normalizeOptions)
    if (!blessed) {
      console.log(`[compare] ${scenario.id}: missing blessed snapshot`)
      index.push({ id: scenario.id, ok: false, reason: "missing_blessed" })
      failed = true
      failCount += 1
      missingBlessed += 1
      continue
    }
    if (!candidate) {
      console.log(`[compare] ${scenario.id}: missing candidate snapshot`)
      index.push({ id: scenario.id, ok: false, reason: "missing_candidate" })
      failed = true
      failCount += 1
      missingCandidate += 1
      continue
    }
    if (candidate !== blessed) {
      console.log(`[compare] ${scenario.id}: mismatch`)
      const diff = diffSample(blessed, candidate, 12)
      const diffFile = path.join(diffRoot, `${scenario.id}.diff.txt`)
      const summaryFile = path.join(diffRoot, `${scenario.id}.summary.json`)
      const lines = [
        `Scenario: ${scenario.id}`,
        `Expected lines: ${diff.expectedLines}`,
        `Actual lines: ${diff.actualLines}`,
        `Mismatched lines: ${diff.mismatches}`,
        "",
        "Samples:",
        ...diff.samples.flatMap((sample) => [
          `- Line ${sample.line}:`,
          `  expected: ${stripAnsi(sample.expected)}`,
          `  actual:   ${stripAnsi(sample.actual)}`,
          "",
        ]),
      ]
      await fs.writeFile(diffFile, lines.join("\n"), "utf8")
      await fs.writeFile(summaryFile, `${JSON.stringify(diff, null, 2)}\n`, "utf8")
      index.push({ id: scenario.id, ok: false, diff: diffFile, summary: summaryFile })
      failed = true
      failCount += 1
      continue
    }
    if (
      styleOptions.require_ansi ||
      styleOptions.diff_add ||
      styleOptions.diff_del ||
      styleOptions.diff_add_bg ||
      styleOptions.diff_del_bg ||
      styleOptions.tool_dot ||
      styleOptions.collapse_dim
    ) {
      const lines = candidateRaw.split(/\r?\n/)
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
      if (styleOptions.diff_add && !addAnsi && !lines.some((line) => /\+\s/.test(stripAnsi(line)))) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing add diff marker)`)
        index.push({ id: scenario.id, ok: false, reason: "style_add_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.diff_del && !delAnsi && !lines.some((line) => /-\s/.test(stripAnsi(line)))) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing del diff marker)`)
        index.push({ id: scenario.id, ok: false, reason: "style_del_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.diff_add_bg && !addBg && !lines.some((line) => /\+\s/.test(stripAnsi(line)))) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing add background)`)
        index.push({ id: scenario.id, ok: false, reason: "style_add_bg_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.diff_del_bg && !delBg && !lines.some((line) => /-\s/.test(stripAnsi(line)))) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing del background)`)
        index.push({ id: scenario.id, ok: false, reason: "style_del_bg_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.tool_dot && !toolDotLine) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing tool dot marker)`)
        index.push({ id: scenario.id, ok: false, reason: "style_tool_dot_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.collapse_dim && !collapseDim) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing collapse dim)`)
        index.push({ id: scenario.id, ok: false, reason: "style_collapse_dim_missing" })
        failed = true
        failCount += 1
        continue
      }
      if (styleOptions.require_ansi && !addAnsi && !delAnsi && !lines.some((line) => /\+\s|-\s/.test(stripAnsi(line)))) {
        console.log(`[compare] ${scenario.id}: style mismatch (missing ANSI)`)
        index.push({ id: scenario.id, ok: false, reason: "style_ansi_missing" })
        failed = true
        failCount += 1
        continue
      }
    }
    console.log(`[compare] ${scenario.id}: ok`)
    index.push({ id: scenario.id, ok: true })
    okCount += 1
  }

  const indexPath = path.join(diffRoot, "index.json")
  await fs.writeFile(
    indexPath,
    `${JSON.stringify({ okCount, failCount, missingBlessed, missingCandidate, index }, null, 2)}\n`,
    "utf8",
  )
  if (options.summary) {
    const total = okCount + failCount
    console.log(
      `[compare] scenarios=${total} ok=${okCount} fail=${failCount} missing_blessed=${missingBlessed} missing_candidate=${missingCandidate}`,
    )
  }
  if (failed && options.strict) process.exit(1)
}

main().catch((error) => {
  console.error("[compare_tui_goldens] failed:", error)
  process.exit(1)
})
