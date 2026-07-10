import { promises as fs } from "node:fs"
import path from "node:path"

export type WidthEquivalenceScope =
  | "owned-visible-region"
  | "warm-landing"
  | "bottom-band"
  | "structured-block"
  | "semantic-current-window"

export interface WidthEquivalenceEndpoint {
  readonly caseDir: string
  readonly checkpoint?: string
  readonly file?: string
}

export interface WidthEquivalencePairConfig {
  readonly id: string
  readonly scope?: WidthEquivalenceScope
  readonly pathA: WidthEquivalenceEndpoint
  readonly pathB: WidthEquivalenceEndpoint
}

export interface WidthEquivalenceDifference {
  readonly line: number
  readonly pathA?: string
  readonly pathB?: string
  readonly severity: "release-blocking" | "warning"
  readonly reason: string
}

export interface WidthEquivalenceReport {
  readonly schemaVersion: 1
  readonly caseId: string
  readonly verdict: "pass" | "fail"
  readonly scope: WidthEquivalenceScope
  readonly comparedLines: number
  readonly normalizedPathA: string[]
  readonly normalizedPathB: string[]
  readonly differences: WidthEquivalenceDifference[]
  readonly ignoredDifferences: Array<{ readonly reason: string; readonly count: number }>
}

const ANSI_PATTERN = /\u001b\[[0-?]*[ -/]*[@-~]/g
const DEFAULT_SCOPE: WidthEquivalenceScope = "owned-visible-region"

const parseArgs = (): { pairConfig: string } => {
  const args = process.argv.slice(2)
  let pairConfig: string | undefined
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--pair-config") pairConfig = args[++index]
  }
  if (!pairConfig) throw new Error("--pair-config is required")
  return { pairConfig: path.resolve(pairConfig) }
}

const readJson = async <T>(file: string): Promise<T> => JSON.parse(await fs.readFile(file, "utf8")) as T

const readFirstExisting = async (files: readonly string[]): Promise<{ file: string; body: string }> => {
  for (const file of files) {
    try {
      return { file, body: await fs.readFile(file, "utf8") }
    } catch {
      // Continue through precedence list.
    }
  }
  throw new Error(`No readable width-equivalence text export found. Tried: ${files.join(", ")}`)
}

const endpointCandidates = (endpoint: WidthEquivalenceEndpoint): string[] => {
  const caseDir = path.resolve(endpoint.caseDir)
  if (endpoint.file) return [path.resolve(caseDir, endpoint.file)]
  const checkpoint = endpoint.checkpoint ?? "visible_final"
  const observerDir = path.join(caseDir, "observer_text")
  const terminalTextDir = path.join(caseDir, "terminal_text")
  return [
    path.join(observerDir, `${checkpoint}.ghostty_screen.txt`),
    path.join(observerDir, `${checkpoint}.tmux.txt`),
    path.join(observerDir, `${checkpoint}.txt`),
    path.join(terminalTextDir, "visible_final.txt"),
  ]
}

export const normalizeWidthEquivalenceLine = (line: string): string =>
  line
    .replace(ANSI_PATTERN, "")
    .replace(/\r/g, "")
    .replace(/\/shared_folders\/querylake_server\/ray_testing\/ray_SCE\/breadboard_repo_tui_runtime_20260409/g, "$ROOT")
    .replace(/\bs [0-9a-f]{8}\b/g, "s $SESSION")
    .replace(/\b\d{4}-\d{2}-\d{2}T[^\s]+/g, "$TIME")
    .replace(/\b\d+ms\b/g, "$DURATION")
    .replace(/[ \t]+$/g, "")

const trimOuterBlankRows = (lines: readonly string[]): string[] => {
  let start = 0
  let end = lines.length
  while (start < end && lines[start].trim() === "") start += 1
  while (end > start && lines[end - 1].trim() === "") end -= 1
  return lines.slice(start, end)
}

const dropObserverMetadata = (lines: readonly string[]): string[] =>
  lines.filter((line) => {
    if (/^={3,}\s*[^=]+\s*={3,}$/.test(line)) return false
    if (/^Screen capture\b/i.test(line)) return false
    if (/^Window\s+0x[0-9a-f]+\b/i.test(line)) return false
    if (/^Geometry:\s*\d+x\d+/i.test(line)) return false
    return true
  })

const firstIndexMatching = (lines: readonly string[], predicates: readonly RegExp[]): number => {
  for (let index = 0; index < lines.length; index += 1) {
    if (predicates.some((pattern) => pattern.test(lines[index]))) return index
  }
  return -1
}

const lastIndexMatching = (lines: readonly string[], predicates: readonly RegExp[]): number => {
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    if (predicates.some((pattern) => pattern.test(lines[index]))) return index
  }
  return -1
}

const sliceThroughFinalPrompt = (lines: readonly string[], start: number): string[] => {
  const finalPrompt = lastIndexMatching(lines, [/^\s*❯(?:\s|$)/, /^\s*>\s*$/])
  const end = finalPrompt >= start ? finalPrompt + 1 : lines.length
  return lines.slice(start, end)
}

export const extractWidthEquivalenceRegion = (body: string, scope: WidthEquivalenceScope = DEFAULT_SCOPE): string[] => {
  const baseLines = trimOuterBlankRows(dropObserverMetadata(body.split(/\n/)).map(normalizeWidthEquivalenceLine))
  if (baseLines.length === 0) return []

  if (scope === "bottom-band") {
    const finalPrompt = lastIndexMatching(baseLines, [/^\s*❯(?:\s|$)/, /^\s*>\s*$/])
    if (finalPrompt < 0) return baseLines.slice(-6)
    const start = Math.max(0, finalPrompt - 4)
    return trimOuterBlankRows(baseLines.slice(start, finalPrompt + 1))
  }

  const landingStart = firstIndexMatching(baseLines, [/BreadBoard/, /Breadboard/])
  const promptStart = firstIndexMatching(baseLines, [/^\s*❯(?:\s|$)/, /^\s*>\s*\S+/])
  const markdownStart = firstIndexMatching(baseLines, [/^\s*#{1,3}\s+\S/, /^\s*-\s+\S/, /^\s*┌/])

  if (scope === "warm-landing") {
    const start = landingStart >= 0 ? landingStart : 0
    return sliceThroughFinalPrompt(baseLines, start)
  }

  if (scope === "structured-block") {
    const start = markdownStart >= 0 ? markdownStart : (promptStart >= 0 ? promptStart : 0)
    return sliceThroughFinalPrompt(baseLines, start)
  }

  const startCandidates = [landingStart, promptStart, markdownStart].filter((value) => value >= 0)
  const start = startCandidates.length > 0 ? Math.min(...startCandidates) : 0
  return sliceThroughFinalPrompt(baseLines, start)
}

const countMatches = (lines: readonly string[], pattern: RegExp): number => lines.filter((line) => pattern.test(line)).length

const invariantDifferences = (label: "pathA" | "pathB", lines: readonly string[]): WidthEquivalenceDifference[] => {
  const differences: WidthEquivalenceDifference[] = []
  const promptCount = countMatches(lines, /^\s*❯(?:\s|$)/)
  const barePromptCount = countMatches(lines, /^\s*❯\s*$/)
  const landingCount = countMatches(lines, /BreadBoard|Breadboard/)
  const footerCount = countMatches(lines, /^\s*(?:[*.\-\\|/]|\[[a-z]+\]|[a-z]+)\s*\[(?:ready|responding|thinking|working|halted|error|disconnected)\]/i)

  if (promptCount > 2) differences.push({ line: 0, severity: "release-blocking", reason: `${label} has ${promptCount} prompt rows after normalization.` })
  if (barePromptCount > 1) differences.push({ line: 0, severity: "release-blocking", reason: `${label} has ${barePromptCount} bare prompt rows after normalization.` })
  if (landingCount > 1) differences.push({ line: 0, severity: "release-blocking", reason: `${label} has ${landingCount} landing/header rows after normalization.` })
  if (footerCount > 1) differences.push({ line: 0, severity: "release-blocking", reason: `${label} has ${footerCount} footer/status rows after normalization.` })

  for (const [index, line] of lines.entries()) {
    if (line.includes("[200~") || line.includes("[201~")) {
      differences.push({ line: index + 1, [label]: line, severity: "release-blocking", reason: `${label} contains bracketed paste marker.` })
    }
    if (/scene owned|runtime host|prototype|inline-scrollback/i.test(line)) {
      differences.push({ line: index + 1, [label]: line, severity: "release-blocking", reason: `${label} contains internal runtime chrome.` })
    }
  }

  let blankRun = 0
  for (const [index, line] of lines.entries()) {
    if (line.trim() === "") blankRun += 1
    else blankRun = 0
    if (blankRun > 1) {
      differences.push({ line: index + 1, [label]: line, severity: "release-blocking", reason: `${label} contains an interior blank gap over budget.` })
      break
    }
  }
  return differences
}

export const compareWidthEquivalenceTexts = (caseId: string, pathAText: string, pathBText: string, scope: WidthEquivalenceScope = DEFAULT_SCOPE): WidthEquivalenceReport => {
  const normalizedPathA = extractWidthEquivalenceRegion(pathAText, scope)
  const normalizedPathB = extractWidthEquivalenceRegion(pathBText, scope)
  const differences: WidthEquivalenceDifference[] = [
    ...invariantDifferences("pathA", normalizedPathA),
    ...invariantDifferences("pathB", normalizedPathB),
  ]
  const maxLines = Math.max(normalizedPathA.length, normalizedPathB.length)
  for (let index = 0; index < maxLines; index += 1) {
    const a = normalizedPathA[index]
    const b = normalizedPathB[index]
    if (a !== b) {
      differences.push({
        line: index + 1,
        pathA: a,
        pathB: b,
        severity: "release-blocking",
        reason: a == null ? "Path A is missing a semantic line." : b == null ? "Path B is missing a semantic line." : "Normalized owned-region lines differ.",
      })
    }
  }

  return {
    schemaVersion: 1,
    caseId,
    verdict: differences.length === 0 ? "pass" : "fail",
    scope,
    comparedLines: maxLines,
    normalizedPathA,
    normalizedPathB,
    differences,
    ignoredDifferences: [],
  }
}

export const evaluateScrollbackWidthEquivalence = async (pairConfigFile: string): Promise<WidthEquivalenceReport> => {
  const config = await readJson<WidthEquivalencePairConfig>(pairConfigFile)
  const pathA = await readFirstExisting(endpointCandidates(config.pathA))
  const pathB = await readFirstExisting(endpointCandidates(config.pathB))
  void pathA.file
  void pathB.file
  return compareWidthEquivalenceTexts(config.id, pathA.body, pathB.body, config.scope ?? DEFAULT_SCOPE)
}

const run = async () => {
  const { pairConfig } = parseArgs()
  const report = await evaluateScrollbackWidthEquivalence(pairConfig)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (report.verdict === "fail") process.exit(1)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
