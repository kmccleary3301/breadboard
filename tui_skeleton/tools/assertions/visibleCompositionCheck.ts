import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly { readonly id: string; readonly message: string }

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const textFilesToScan = async (caseDir: string): Promise<Array<{ file: string; body: string }>> => {
  const candidates = [
    path.join(caseDir, "terminal_text", "visible_final.txt"),
  ]
  const observerDir = path.join(caseDir, "observer_text")
  const observerEntries = await fs.readdir(observerDir).catch(() => [])
  for (const entry of observerEntries) {
    if (entry.endsWith(".txt") && !entry.endsWith(".x11.txt") && !entry.endsWith(".summary.txt") && !entry.includes("scrollback")) {
      candidates.push(path.join(observerDir, entry))
    }
  }

  const out: Array<{ file: string; body: string }> = []
  for (const file of candidates) {
    try {
      out.push({ file, body: await fs.readFile(file, "utf8") })
    } catch {
      // Harnesses emit different capture surfaces.
    }
  }
  return out
}

const countMatches = (body: string, pattern: RegExp): number => [...body.matchAll(pattern)].length
const countNeedle = (body: string, needle: string): number => body.split(needle).length - 1

const hasSeparatorCollision = (line: string): boolean => {
  if (!/[─━]{8,}/.test(line)) return false
  return /(?:[─━]{8,}.*(?:❯|Run finished)|(?:❯|Run finished).*[─━]{8,})/.test(line)
}

const hasClippedLandingFragment = (body: string): boolean => {
  const lines = body.split(/\r?\n/)
  const firstContentIndex = lines.findIndex((line) =>
    line.includes("❯") ||
    /^\s{0,8}#{1,3}\s+\S/.test(line) ||
    /^\s{0,8}-\s+\S/.test(line),
  )
  const prefix = lines.slice(0, firstContentIndex < 0 ? Math.min(lines.length, 12) : firstContentIndex).join("\n")
  if (!prefix.trim()) return false
  if (prefix.includes("BreadBoard") && prefix.includes("╰")) return false
  return /[╭╰│]/.test(prefix)
}

const hasHorizontalContentCollision = (line: string): boolean => {
  if (/^[\s]*[┌├└].{4,}\s{8,}[┌├└│]/.test(line)) return true
  if (/^\s*-\s+\S.{4,}\s{8,}-\s+\S/.test(line)) return true
  if (/^\s*#{1,3}\s+\S.{4,}\s{8,}-\s+\S/.test(line)) return true
  return false
}

const hasInterruptedTableBeforeComposer = (body: string): boolean => {
  const lines = body.split(/\r?\n/)
  let insideTable = false
  for (const line of lines) {
    if (/^\s*┌[─┬]+┐\s*$/.test(line)) insideTable = true
    if (insideTable && /^\s*└[─┴]+┘\s*$/.test(line)) insideTable = false
    if (insideTable && /^\s*❯(?:\s*$|\s+Try\b)/.test(line)) return true
  }
  return false
}

const isFooterPhaseLine = (line: string): boolean =>
  /^\s*(?:[•✶✻✷*.\-\\|/])\s+\[(?:ready|responding|thinking|working|halted|error|disconnected)\]/i.test(line)

const footerPhasePattern = /(?:[•✶✻✷*.\-\\|/])\s+\[(?:ready|responding|thinking|working|halted|error|disconnected)\]/gi
const footerControlOrSummaryTailPattern = /\s{12,}(?:resume \/sessions|\/sessions recent|ctrl\+o transcript|cmd\+o transcript|@ attach|mdl\s+)/i

const isComposerPromptLine = (line: string): boolean =>
  /^\s*❯(?:\s*$|\s+)/.test(line)

const visibleComposerPromptLines = (body: string): string[] =>
  body.split(/\r?\n/).filter((line) => isComposerPromptLine(line) && !/^\s*❯\s+\S.*\S/.test(line.replace(/Type your request…/, "")))

const hasClippedSplitLanding = (body: string): boolean => {
  const lines = body.split(/\r?\n/)
  const firstPromptIndex = lines.findIndex(isComposerPromptLine)
  const prefix = lines.slice(0, firstPromptIndex < 0 ? Math.min(lines.length, 12) : firstPromptIndex).join("\n")
  if (!prefix.includes("BreadBoard v0.2.0") && !prefix.includes("BreadBoard v0.0.0a")) return false
  const landingArtRows = lines
    .slice(0, firstPromptIndex < 0 ? Math.min(lines.length, 12) : firstPromptIndex)
    .filter((line) => /░█▄▄|░█▄█|█▀█|█▄█/.test(line)).length
  const hasConfig = prefix.includes("Using Config")
  const hasIdentity = /(?:gpt-|dev ·|Codex|Claude Code)/.test(prefix)
  const hasWorkspace = /(?:\/|\\|~|…)/.test(prefix)
  if (prefix.includes("gpt-") && hasConfig && hasWorkspace) return false
  if (landingArtRows >= 4 && hasConfig && hasIdentity && hasWorkspace) return false
  if (landingArtRows >= 2 && hasConfig && /(?:^|\n)\s*(?:●|Proceed to)\b/m.test(prefix)) return false
  return /░█▄▄|░█▄█|Using Config/.test(prefix)
}

const hasFooterPhaseStaleSummaryTail = (line: string): boolean =>
  isFooterPhaseLine(line) && footerControlOrSummaryTailPattern.test(line)

const hasFooterPhaseHorizontalDrift = (line: string): boolean =>
  /^\s{8,}(?:[•✶✻✷*.\-\\|/])\s+\[(?:ready|responding|thinking|working|halted|error|disconnected)\]/i.test(line)

const excessiveBlankGapBeforeFooter = (body: string): number | null => {
  const lines = body.split(/\r?\n/)
  const footerIndex = lines.findIndex(isFooterPhaseLine)
  if (footerIndex < 0) return null
  let blankRows = 0
  for (let index = footerIndex - 1; index >= 0; index -= 1) {
    if (lines[index].trim() !== "") break
    blankRows += 1
  }
  return blankRows > 1 ? blankRows : null
}

const excessiveBlankGapBeforeComposerStart = (body: string): number | null => {
  const lines = body.split(/\r?\n/)
  const footerIndex = lines.findIndex(isFooterPhaseLine)
  if (footerIndex < 0) return null

  let composerStartIndex = footerIndex
  let previousContentIndex = footerIndex - 1
  while (previousContentIndex >= 0 && lines[previousContentIndex].trim() === "") previousContentIndex -= 1
  if (previousContentIndex >= 0 && isComposerPromptLine(lines[previousContentIndex])) {
    composerStartIndex = previousContentIndex
  }

  let blankRows = 0
  let previousContent = ""
  for (let index = composerStartIndex - 1; index >= 0; index -= 1) {
    if (lines[index].trim() !== "") {
      previousContent = lines[index]
      break
    }
    blankRows += 1
  }
  const followsStartupLanding =
    /BreadBoard v|Using Config|gpt-|\/shared_folders|\/home\/|[A-Za-z]:\\/.test(previousContent)
  if (followsStartupLanding && blankRows <= 2) return null
  return blankRows > 1 ? blankRows : null
}

export const evaluateVisibleComposition = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await textFilesToScan(caseDir)
  for (const { file, body } of files) {
    const relative = path.relative(caseDir, file)
    const runFinishedCount = countMatches(body, /Run finished(?:\s*\([^)]*\))?\.?/g)
    if (runFinishedCount > 1) {
      anomalies.push({
        id: "visible-composition-duplicate-run-finished",
        message: `${relative} contains ${runFinishedCount} visible Run finished status lines.`,
      })
    }
    const idlePromptCount = countNeedle(body, '❯ Try "refactor <filepath>"')
    if (idlePromptCount > 1) {
      anomalies.push({
        id: "visible-composition-duplicate-idle-prompt",
        message: `${relative} contains ${idlePromptCount} idle composer prompts.`,
      })
    }
    const footerPhaseCount = countMatches(body, footerPhasePattern)
    if (footerPhaseCount > 1) {
      anomalies.push({
        id: "visible-composition-duplicate-footer-phase",
        message: `${relative} contains ${footerPhaseCount} footer/status phase lines.`,
      })
    }
    const barePromptCount = body.split(/\r?\n/).filter((line) => /^\s*❯\s*$/.test(line)).length
    if (barePromptCount > 1) {
      anomalies.push({
        id: "visible-composition-duplicate-bare-prompt",
        message: `${relative} contains ${barePromptCount} bare composer prompts.`,
      })
    }
    const composerPromptCount = visibleComposerPromptLines(body).length
    if (composerPromptCount > 1) {
      anomalies.push({
        id: "visible-composition-duplicate-composer-prompt",
        message: `${relative} contains ${composerPromptCount} visible composer prompt lines.`,
      })
    }
    if (hasClippedSplitLanding(body)) {
      anomalies.push({
        id: "visible-composition-clipped-split-landing",
        message: `${relative} contains an incomplete split landing before the composer/content region.`,
      })
    }
    if (body.includes("[200~") || body.includes("[201~") || body.includes("\u001b[200~") || body.includes("\u001b[201~")) {
      anomalies.push({
        id: "visible-composition-bracketed-paste-marker",
        message: `${relative} contains bracketed paste control markers.`,
      })
    }

    for (const [index, line] of body.split(/\r?\n/).entries()) {
      if (hasSeparatorCollision(line)) {
        anomalies.push({
          id: "visible-composition-separator-collision",
          message: `${relative}:${index + 1} merges separator rule characters with prompt/status/content: "${line.trim()}".`,
        })
      }
      if (hasHorizontalContentCollision(line)) {
        anomalies.push({
          id: "visible-composition-horizontal-content-collision",
          message: `${relative}:${index + 1} contains horizontally collided content: "${line.trim()}".`,
        })
      }
      if (hasFooterPhaseStaleSummaryTail(line)) {
        anomalies.push({
          id: "visible-composition-footer-phase-stale-summary-tail",
          message: `${relative}:${index + 1} shows a footer phase row with stale right-side summary/control text: "${line.trim()}".`,
        })
      }
      if (hasFooterPhaseHorizontalDrift(line)) {
        anomalies.push({
          id: "visible-composition-footer-phase-horizontal-drift",
          message: `${relative}:${index + 1} shows a footer phase row shifted away from the left edge: "${line.trim()}".`,
        })
      }
    }

    if (path.basename(file).includes("after-width-shrink") && hasClippedLandingFragment(body)) {
      anomalies.push({
        id: "visible-composition-clipped-landing-fragment",
        message: `${relative} begins with a clipped landing/border fragment before the prompt.`,
      })
    }
    if (hasInterruptedTableBeforeComposer(body)) {
      anomalies.push({
        id: "visible-composition-interrupted-table-before-composer",
        message: `${relative} shows a markdown table interrupted by the composer before the table bottom.`,
      })
    }
    const blankGapRows = excessiveBlankGapBeforeFooter(body)
    if (blankGapRows != null) {
      anomalies.push({
        id: "visible-composition-excessive-blank-gap-before-footer",
        message: `${relative} contains ${blankGapRows} blank rows immediately before the footer/status line.`,
      })
    }
    const composerGapRows = excessiveBlankGapBeforeComposerStart(body)
    if (composerGapRows != null) {
      anomalies.push({
        id: "visible-composition-excessive-blank-gap-before-composer",
        message: `${relative} contains ${composerGapRows} blank rows immediately before the active composer/footer region.`,
      })
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateVisibleComposition(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
