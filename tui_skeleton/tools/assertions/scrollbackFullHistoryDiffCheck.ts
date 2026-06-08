import { promises as fs } from "node:fs"
import path from "node:path"

export interface FullHistoryDiffOptions {
  readonly expectedSentinels?: readonly string[]
  readonly landingMarkers?: readonly string[]
  readonly promptMarkers?: readonly string[]
  readonly appMarkers?: readonly string[]
}

export interface FullHistoryFinding {
  readonly id: string
  readonly message: string
  readonly blocking: boolean
}

export interface FullHistoryDiffReport {
  readonly schemaVersion: 1
  readonly caseId: string
  readonly verdict: "pass" | "fail"
  readonly findings: FullHistoryFinding[]
  readonly expectedSentinels: readonly string[]
  readonly artifactsConsumed: readonly string[]
}

const ANSI_PATTERN = /\u001b\[[0-?]*[ -/]*[@-~]/g
const DEFAULT_SENTINELS = ["PRE_APP_ALPHA", "PRE_APP_BETA"] as const
const DEFAULT_LANDING_MARKERS = ["BreadBoard · Claude Code", "BreadBoard v0.2.0", "BreadBoard v0.0.0a"] as const
const DEFAULT_APP_MARKERS = ["BreadBoard", "❯", "## Streaming", "# Streaming", "Run finished"] as const

const parseArgs = (): { caseDir: string; config?: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  let config: string | undefined
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--case-dir") caseDir = args[++index]
    else if (args[index] === "--config") config = args[++index]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir), config: config ? path.resolve(config) : undefined }
}

const readOptional = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const readJsonOptional = async <T>(file: string): Promise<T | undefined> => {
  const text = await readOptional(file)
  if (!text.trim()) return undefined
  return JSON.parse(text) as T
}

const normalize = (text: string): string => text.replace(ANSI_PATTERN, "").replace(/\r/g, "")

const countNeedle = (haystack: string, needle: string): number => haystack.split(needle).length - 1

const combineScrollbackAndScreen = (scrollback: string, screen: string): string => {
  const scrollbackLines = normalize(scrollback).split("\n")
  const screenLines = normalize(screen).split("\n")
  let overlap = 0
  const maxOverlap = Math.min(scrollbackLines.length, screenLines.length)
  for (let size = 1; size <= maxOverlap; size += 1) {
    const scrollbackSuffix = scrollbackLines.slice(scrollbackLines.length - size).join("\n")
    const screenPrefix = screenLines.slice(0, size).join("\n")
    if (scrollbackSuffix === screenPrefix) overlap = size
  }
  return [...scrollbackLines, ...screenLines.slice(overlap)].join("\n")
}

const readHistoryArtifacts = async (caseDir: string): Promise<Array<{ file: string; body: string }>> => {
  const out: Array<{ file: string; body: string }> = []
  const terminalScrollback = path.join(caseDir, "terminal_text", "scrollback_final.txt")
  const terminalBody = await readOptional(terminalScrollback)
  if (terminalBody.trim()) out.push({ file: path.relative(caseDir, terminalScrollback), body: terminalBody })

  const observerDir = path.join(caseDir, "observer_text")
  const entries = await fs.readdir(observerDir).catch(() => [])
  const ghosttyScreens = new Map<string, string>()
  const ghosttyScrollbacks = new Map<string, string>()
  for (const entry of entries.sort()) {
    if (!entry.endsWith(".txt")) continue
    if (entry.endsWith(".summary.txt") || entry.endsWith(".x11.txt")) continue
    if (!entry.includes("ghostty_screen") && !entry.includes("ghostty_scrollback") && !entry.includes("tmux_scrollback")) continue
    const file = path.join(observerDir, entry)
    const body = await readOptional(file)
    if (!body.trim()) continue
    out.push({ file: path.relative(caseDir, file), body })
    const ghosttyScreenMatch = entry.match(/^(.*)\.ghostty_screen\.txt$/)
    const ghosttyScrollbackMatch = entry.match(/^(.*)\.ghostty_scrollback\.txt$/)
    if (ghosttyScreenMatch) ghosttyScreens.set(ghosttyScreenMatch[1], body)
    if (ghosttyScrollbackMatch) ghosttyScrollbacks.set(ghosttyScrollbackMatch[1], body)
  }
  for (const [label, scrollback] of ghosttyScrollbacks.entries()) {
    const screen = ghosttyScreens.get(label)
    if (!screen) continue
    out.push({
      file: path.join("observer_text", `${label}.ghostty_combined_state.txt`),
      body: combineScrollbackAndScreen(scrollback, screen),
    })
  }
  return out
}

const bestArtifactForSentinels = (artifacts: readonly { file: string; body: string }[], sentinels: readonly string[]): { file: string; body: string } | undefined => {
  let best: { file: string; body: string; score: number } | undefined
  for (const artifact of artifacts) {
    const normalized = normalize(artifact.body)
    const score = sentinels.reduce((sum, sentinel) => sum + (normalized.includes(sentinel) ? 1 : 0), 0)
    if (!best || score > best.score) best = { ...artifact, score }
  }
  return best
}

const bestArtifactForDurableHistory = (
  artifacts: readonly { file: string; body: string }[],
  sentinels: readonly string[],
  landingMarkers: readonly string[],
  promptMarkers: readonly string[],
): { file: string; body: string } | undefined => {
  let best: { file: string; body: string; score: number } | undefined
  for (const artifact of artifacts) {
    const normalized = normalize(artifact.body)
    const score =
      sentinels.reduce((sum, sentinel) => sum + (normalized.includes(sentinel) ? 3 : 0), 0) +
      landingMarkers.reduce((sum, marker) => sum + (normalized.includes(marker) ? 3 : 0), 0) +
      promptMarkers.reduce((sum, marker) => sum + (normalized.includes(marker) ? 2 : 0), 0) +
      (artifact.file.includes("ghostty_combined_state") ? 1 : 0) +
      (normalized.includes("## Streaming") || normalized.includes("# Streaming") ? 1 : 0)
    if (!best || score > best.score) best = { ...artifact, score }
  }
  return best
}

const addFinding = (findings: FullHistoryFinding[], id: string, message: string): void => {
  findings.push({ id, message, blocking: true })
}

const evaluateSentinels = (findings: FullHistoryFinding[], text: string, sentinels: readonly string[], appMarkers: readonly string[]): void => {
  let previousIndex = -1
  for (const sentinel of sentinels) {
    const first = text.indexOf(sentinel)
    const hits = countNeedle(text, sentinel)
    if (first < 0) {
      addFinding(findings, "SCR-HIST-001", `pre-app sentinel missing: expected ${sentinel}.`)
      continue
    }
    if (hits > 1) {
      addFinding(findings, "SCR-HIST-006", `pre-app sentinel duplicated: expected ${sentinel} once, found ${hits}.`)
    }
    if (first < previousIndex) {
      addFinding(findings, "SCR-HIST-002", `pre-app sentinel reordered: ${sentinel} appeared before an earlier sentinel.`)
    }
    previousIndex = first
  }

  const lastSentinel = sentinels.map((sentinel) => text.indexOf(sentinel)).filter((index) => index >= 0).sort((a, b) => a - b).at(-1) ?? -1
  if (lastSentinel >= 0) {
    const preAppText = text.slice(0, lastSentinel)
    for (const marker of appMarkers) {
      if (preAppText.includes(marker)) {
        addFinding(findings, "SCR-HIST-003", `app output appeared before pre-app boundary marker: ${marker}.`)
        break
      }
    }
  }
}

const evaluatePromptMarkers = (findings: FullHistoryFinding[], text: string, promptMarkers: readonly string[]): void => {
  for (const marker of promptMarkers) {
    const hits = countNeedle(text, marker)
    if (hits === 0) addFinding(findings, "SCR-HIST-007", `committed prompt missing: expected ${marker}.`)
    if (hits > 1) addFinding(findings, "SCR-HIST-005", `committed turn duplicated: expected prompt marker ${marker} once, found ${hits}.`)
  }
}

const evaluateLanding = (findings: FullHistoryFinding[], text: string, landingMarkers: readonly string[]): void => {
  const landingHits = landingMarkers.reduce((sum, marker) => sum + countNeedle(text, marker), 0)
  if (landingHits === 0) addFinding(findings, "SCR-HIST-008", "landing/header missing from durable app history artifact.")
  if (landingHits > 1) addFinding(findings, "SCR-HIST-004", `landing duplicated in durable app history: expected at most 1, found ${landingHits}.`)
}

export const evaluateScrollbackFullHistoryDiff = async (caseDir: string, options: FullHistoryDiffOptions = {}): Promise<FullHistoryDiffReport> => {
  const artifacts = await readHistoryArtifacts(caseDir)
  const sentinels = options.expectedSentinels ?? DEFAULT_SENTINELS
  const landingMarkers = options.landingMarkers ?? DEFAULT_LANDING_MARKERS
  const appMarkers = options.appMarkers ?? DEFAULT_APP_MARKERS
  const findings: FullHistoryFinding[] = []

  if (artifacts.length === 0) {
    addFinding(findings, "SCR-HIST-000", "no scrollback/screen history artifacts were found.")
  }

  const sentinelArtifact = bestArtifactForSentinels(artifacts, sentinels)
  if (!sentinelArtifact) {
    addFinding(findings, "SCR-HIST-000", "no candidate artifact was available for sentinel verification.")
  } else {
    evaluateSentinels(findings, normalize(sentinelArtifact.body), sentinels, appMarkers)
  }

  const durableArtifact = bestArtifactForDurableHistory(artifacts, sentinels, landingMarkers, options.promptMarkers ?? []) ?? sentinelArtifact
  if (durableArtifact) {
    const durableText = normalize(durableArtifact.body)
    evaluateLanding(findings, durableText, landingMarkers)
    evaluatePromptMarkers(findings, durableText, options.promptMarkers ?? [])
  }

  const anchor = await readJsonOptional<Record<string, unknown>>(path.join(caseDir, "app_start_anchor.txt"))
  if (anchor) {
    if (anchor.mode !== "preserved-scrollback") {
      addFinding(findings, "SCR-HIST-009", `app-start anchor mode is ${String(anchor.mode)}, expected preserved-scrollback.`)
    }
    if (anchor.preAppHistoryPolicy != null && anchor.preAppHistoryPolicy !== "untouched") {
      addFinding(findings, "SCR-HIST-010", "app-start anchor does not preserve untouched pre-app history policy.")
    }
  }

  return {
    schemaVersion: 1,
    caseId: path.basename(caseDir),
    verdict: findings.length === 0 ? "pass" : "fail",
    findings,
    expectedSentinels: sentinels,
    artifactsConsumed: artifacts.map((artifact) => artifact.file),
  }
}

const run = async () => {
  const { caseDir, config } = parseArgs()
  const options = config ? await readJsonOptional<FullHistoryDiffOptions>(config) : undefined
  const report = await evaluateScrollbackFullHistoryDiff(caseDir, options ?? {})
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (report.verdict === "fail") process.exit(1)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
