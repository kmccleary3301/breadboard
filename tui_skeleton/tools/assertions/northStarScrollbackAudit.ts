import { promises as fs } from "node:fs"
import path from "node:path"
import { evaluateStartupLandingGap } from "./startupLandingGapCheck.ts"
import { evaluateVisibleComposition } from "./visibleCompositionCheck.ts"
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateScrollbackGeometry } from "./scrollbackGeometryCheck.ts"
import { evaluateLandingLifecycle } from "./landingLifecycleCheck.ts"
import { evaluateOverlayFootprint } from "./overlayFootprintCheck.ts"
import { evaluateScrollbackFullHistoryDiff } from "./scrollbackFullHistoryDiffCheck.ts"

interface Finding {
  readonly caseId: string
  readonly file?: string
  readonly id: string
  readonly message: string
}

const ESC = "\u001b"
const FORBIDDEN = [
  { id: "alt-buffer-enter", label: "alt-buffer enter", sequence: `${ESC}[?1049h` },
  { id: "clear-screen", label: "clear screen", sequence: `${ESC}[2J` },
  { id: "clear-scrollback", label: "clear scrollback", sequence: `${ESC}[3J` },
  { id: "terminal-reset", label: "terminal reset", sequence: `${ESC}c` },
]

const LANDING_HEADER_MARKERS = [
  "BreadBoard v0.2.0",
  "BreadBoard v0.0.0a",
  "BreadBoard · Claude Code",
]

const readOptional = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const exists = async (file: string): Promise<boolean> => {
  try {
    await fs.access(file)
    return true
  } catch {
    return false
  }
}

const walkFiles = async (root: string): Promise<string[]> => {
  const out: string[] = []
  const visit = async (dir: string) => {
    const entries = await fs.readdir(dir, { withFileTypes: true }).catch(() => [])
    for (const entry of entries) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) await visit(full)
      else if (entry.isFile()) out.push(full)
    }
  }
  await visit(root)
  return out.sort()
}

const count = (text: string, needle: string): number => text.split(needle).length - 1
const countLandingHeaders = (text: string): number =>
  LANDING_HEADER_MARKERS.reduce((sum, marker) => sum + count(text, marker), 0)

const isCaseDir = async (dir: string): Promise<boolean> =>
  (await exists(path.join(dir, "case_info.json"))) ||
  (await exists(path.join(dir, "terminal_text", "scrollback_final.txt"))) ||
  (await exists(path.join(dir, "observer_text")))

const resolveCaseDirs = async (root: string): Promise<string[]> => {
  const stat = await fs.stat(root)
  if (!stat.isDirectory()) throw new Error(`${root} is not a directory`)
  if (await isCaseDir(root)) return [root]
  const entries = await fs.readdir(root, { withFileTypes: true })
  const dirs: string[] = []
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const full = path.join(root, entry.name)
    if (await isCaseDir(full)) dirs.push(full)
  }
  return dirs.sort()
}

const shouldAuditTextExport = (relative: string): boolean => {
  const normalized = relative.replace(/\\/g, "/")
  if (normalized.includes("/observer_text/") || normalized.startsWith("observer_text/")) {
    if (normalized.endsWith(".summary.txt")) return false
    if (normalized.endsWith(".x11.txt")) return false
    if (normalized.endsWith(".png")) return false
    return normalized.endsWith(".txt")
  }
  return normalized === "terminal_text/scrollback_final.txt" || normalized === "terminal_text/visible_final.txt"
}

const isFullScrollbackExport = (relative: string): boolean => {
  const normalized = relative.replace(/\\/g, "/")
  const basename = path.basename(normalized)
  return (
    normalized === "terminal_text/scrollback_final.txt" ||
    basename === "after-width-shrink.tmux_scrollback.txt" ||
    basename === "streaming-final.tmux_scrollback.txt"
  )
}

const isGhosttyNativeStateExport = (relative: string): boolean => {
  const normalized = relative.replace(/\\/g, "/")
  const basename = path.basename(normalized)
  return basename.endsWith(".ghostty_scrollback.txt") || basename.endsWith(".ghostty_screen.txt")
}

const isStartupVisibleExport = (relative: string): boolean => {
  const normalized = relative.replace(/\\/g, "/")
  return normalized.includes("/startup.") || normalized.endsWith("/startup.txt") || normalized === "terminal_text/visible_final.txt"
}

const auditForbiddenSequences = (caseId: string, relative: string, text: string): Finding[] => {
  const findings: Finding[] = []
  for (const forbidden of FORBIDDEN) {
    const hits = count(text, forbidden.sequence)
    if (hits > 0) {
      findings.push({
        caseId,
        file: relative,
        id: forbidden.id,
        message: `Forbidden ${forbidden.label} sequence appeared ${hits} time(s).`,
      })
    }
  }
  return findings
}

const auditCase = async (caseDir: string): Promise<Finding[]> => {
  const findings: Finding[] = []
  const caseId = path.basename(caseDir)
  const anchorText = await readOptional(path.join(caseDir, "app_start_anchor.txt"))
  if (anchorText && !anchorText.includes('"mode": "preserved-scrollback"')) {
    findings.push({ caseId, id: "anchor-mode-mismatch", message: "App-start anchor is not preserved-scrollback." })
  }
  if (anchorText && !anchorText.includes('"preAppHistoryPolicy": "untouched"')) {
    findings.push({ caseId, id: "anchor-prehistory-policy-mismatch", message: "App-start anchor does not declare untouched pre-app history." })
  }

  const allFiles = await walkFiles(caseDir)
  const textExports = allFiles.filter((file) => shouldAuditTextExport(path.relative(caseDir, file)))
  if (textExports.length === 0) {
    findings.push({ caseId, id: "no-host-exports", message: "No host scrollback/screen text exports were found." })
  }

  const startupAnomalies = await evaluateStartupLandingGap(caseDir)
  for (const anomaly of startupAnomalies) {
    findings.push({ caseId, id: anomaly.id, message: anomaly.message })
  }
  const compositionAnomalies = [
    ...await evaluateUiChromeBanlist(caseDir),
    ...await evaluateHiddenEntryAffordance(caseDir),
    ...await evaluateStatusVocabulary(caseDir),
    ...await evaluateVisibleComposition(caseDir),
    ...await evaluateScrollbackGeometry(caseDir),
    ...await evaluateLandingLifecycle(caseDir),
    ...await evaluateOverlayFootprint(caseDir),
  ]
  for (const anomaly of compositionAnomalies) {
    findings.push({ caseId, id: anomaly.id, message: anomaly.message })
  }
  const fullHistoryReport = await evaluateScrollbackFullHistoryDiff(caseDir)
  for (const finding of fullHistoryReport.findings) {
    findings.push({ caseId, id: finding.id, message: finding.message })
  }

  const fullScrollbackExports = textExports.filter((file) => isFullScrollbackExport(path.relative(caseDir, file)))
  const ghosttyNativeStateExports = textExports.filter((file) => isGhosttyNativeStateExport(path.relative(caseDir, file)))
  const hasGhosttyNativeFullState = ghosttyNativeStateExports.length > 0
  if (fullScrollbackExports.length === 0 && !hasGhosttyNativeFullState) {
    findings.push({ caseId, id: "no-full-scrollback-export", message: "No full scrollback text export was found." })
  }

  for (const file of textExports) {
    const relative = path.relative(caseDir, file)
    const text = await readOptional(file)
    findings.push(...auditForbiddenSequences(caseId, relative, text))

    const landingCount = countLandingHeaders(text)
    const fullScrollbackExport = isFullScrollbackExport(relative) && !(hasGhosttyNativeFullState && relative.replace(/\\/g, "/") === "terminal_text/scrollback_final.txt")
    if (fullScrollbackExport) {
      if (!text.includes("PRE_APP_ALPHA") || !text.includes("PRE_APP_BETA")) {
        findings.push({ caseId, file: relative, id: "pre-app-history-missing", message: "Full scrollback export is missing seeded pre-app shell history." })
      }
      if (landingCount !== 1) {
        findings.push({ caseId, file: relative, id: "landing-count-mismatch", message: `Expected exactly one landing header in full scrollback, found ${landingCount}.` })
      }
    } else if (isStartupVisibleExport(relative) && !relative.includes("terminal_text/visible_final")) {
      if (landingCount !== 1) {
        findings.push({ caseId, file: relative, id: "startup-visible-landing-mismatch", message: `Expected exactly one landing header in startup visible export, found ${landingCount}.` })
      }
    }
  }

  if (hasGhosttyNativeFullState) {
    const combinedGhosttyStateParts = await Promise.all(ghosttyNativeStateExports.map((file) => readOptional(file)))
    const combinedGhosttyState = combinedGhosttyStateParts.join("\n")
    const landingCount = countLandingHeaders(combinedGhosttyState)
    if (!combinedGhosttyState.includes("PRE_APP_ALPHA") || !combinedGhosttyState.includes("PRE_APP_BETA")) {
      findings.push({ caseId, file: "observer_text/*.ghostty_{screen,scrollback}.txt", id: "pre-app-history-missing", message: "Combined Ghostty native screen+scrollback state is missing seeded pre-app shell history." })
    }
    if (landingCount < 1) {
      findings.push({ caseId, file: "observer_text/*.ghostty_{screen,scrollback}.txt", id: "landing-count-mismatch", message: "Combined Ghostty native screen+scrollback state is missing the landing header." })
    }
  }

  const anomaliesText = await readOptional(path.join(caseDir, "anomalies.json"))
  if (anomaliesText.trim()) {
    try {
      const anomalies = JSON.parse(anomaliesText)
      if (Array.isArray(anomalies) && anomalies.length > 0) {
        findings.push({ caseId, file: "anomalies.json", id: "case-anomalies-present", message: `Case reported ${anomalies.length} anomaly/anomalies.` })
      }
    } catch {
      findings.push({ caseId, file: "anomalies.json", id: "case-anomalies-invalid", message: "Could not parse anomalies.json." })
    }
  }

  return findings
}

const main = async () => {
  const target = process.argv[2]
  if (!target) throw new Error("usage: northStarScrollbackAudit.ts <case-dir-or-batch-dir>")
  const root = path.resolve(target)
  const caseDirs = await resolveCaseDirs(root)
  const findings = (await Promise.all(caseDirs.map(auditCase))).flat()
  const summary = { root, cases: caseDirs.length, findings }
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`)
  if (findings.length > 0) process.exitCode = 1
}

void main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
