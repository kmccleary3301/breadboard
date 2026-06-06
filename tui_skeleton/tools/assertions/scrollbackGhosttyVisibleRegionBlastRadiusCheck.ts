import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { promises as fs } from "node:fs"
import path from "node:path"
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"
import { evaluateVisibleComposition } from "./visibleCompositionCheck.ts"
import { evaluateStartupLandingGap } from "./startupLandingGapCheck.ts"
import { evaluateLandingLifecycle } from "./landingLifecycleCheck.ts"
import { evaluateOverlayFootprint } from "./overlayFootprintCheck.ts"

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

const readFirstExisting = async (files: readonly string[]): Promise<string> => {
  for (const file of files) {
    try {
      return await fs.readFile(file, "utf8")
    } catch {
      // Continue through the capture precedence list.
    }
  }
  return ""
}

const readLabel = async (caseDir: string, label: string, scrollback = false): Promise<string> => {
  const observerDir = path.join(caseDir, "observer_text")
  return readFirstExisting(
    scrollback
      ? [path.join(observerDir, `${label}.tmux_scrollback.txt`), path.join(observerDir, `${label}.ghostty_scrollback.txt`)]
      : [path.join(observerDir, `${label}.tmux.txt`), path.join(observerDir, `${label}.ghostty_screen.txt`)],
  )
}

const countNonblank = (value: string): number => value.split(/\r?\n/).filter((line) => line.trim().length > 0).length
const countNeedle = (text: string, needle: string): number => text.split(needle).length - 1

const hasFirstTurnContext = (text: string): boolean =>
  text.includes("Write a markdown answer") ||
  text.includes("# Streaming") ||
  text.includes("## Streaming") ||
  text.includes("first item") ||
  text.includes("console.log('ghostty')")

const hasUnsettledSettledSnapshot = (text: string): boolean =>
  /\[(responding|thinking|working)\]/i.test(text) ||
  /elapsed\s+\d+s\s+.*esc interrupt/i.test(text) ||
  /Assistant responding/i.test(text)

const duplicateId = (scope: "visible" | "scrollback", needle: string): string =>
  `after-shrink-${scope}-duplicate-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`

export const evaluateScrollbackGhosttyVisibleRegionBlastRadius = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  anomalies.push(...await evaluateVisibleComposition(caseDir))
  anomalies.push(...await evaluateStartupLandingGap(caseDir))
  anomalies.push(...await evaluateLandingLifecycle(caseDir))
  anomalies.push(...await evaluateOverlayFootprint(caseDir))
  const anchorText = await fs.readFile(path.join(caseDir, "app_start_anchor.txt"), "utf8").catch(() => "")
  const boundsText = await fs.readFile(path.join(caseDir, "managed_region_bounds.ndjson"), "utf8").catch(() => "")
  const turn1 = await readLabel(caseDir, "turn1-settled")
  const turn1Scrollback = await readLabel(caseDir, "turn1-settled", true)
  const shrink = await readLabel(caseDir, "after-width-shrink")
  const shrinkScrollback = await readLabel(caseDir, "after-width-shrink", true)
  const turn2 = await readLabel(caseDir, "turn2-settled")
  const turn2Scrollback = await readLabel(caseDir, "turn2-settled", true)
  const turn1Combined = `${turn1}\n${turn1Scrollback}`
  const shrinkCombined = `${shrink}\n${shrinkScrollback}`
  const turn2Combined = `${turn2}\n${turn2Scrollback}`

  if (!anchorText.includes('"mode": "preserved-scrollback"')) {
    anomalies.push({ id: "anchor-missing", message: "Ghostty width-shrink case did not emit a preserved-scrollback app-start anchor." })
  }
  if (!boundsText.trim()) {
    anomalies.push({ id: "managed-region-bounds-missing", message: "Ghostty width-shrink case did not emit managed-region bounds." })
  }
  if (!hasFirstTurnContext(turn1Combined)) {
    anomalies.push({ id: "turn1-context-missing", message: "Ghostty turn1-settled does not show first-turn content before resize." })
  }
  if (hasUnsettledSettledSnapshot(turn1Combined)) {
    anomalies.push({ id: "turn1-settled-still-active", message: "Ghostty turn1-settled snapshot still shows an active response state." })
  }
  if (countNonblank(shrink) < 8) {
    anomalies.push({ id: "after-shrink-visible-near-blank", message: "Ghostty visible pane collapsed to a near-blank frame after width shrink." })
  }
  if (!hasFirstTurnContext(shrink)) {
    anomalies.push({ id: "after-shrink-visible-turn-context-missing", message: "Ghostty visible pane lost first-turn context after width shrink." })
  }
  if (!hasFirstTurnContext(shrinkCombined)) {
    anomalies.push({ id: "after-shrink-scrollback-turn-context-missing", message: "Ghostty scrollback capture lost first-turn context after width shrink." })
  }
  if (turn2Combined.trim() && !turn2Combined.includes("Write a second markdown answer after the resize")) {
    anomalies.push({ id: "turn2-context-missing", message: "Ghostty turn2-settled does not show the second prompt context after resize." })
  }
  if (turn2Combined.trim() && hasUnsettledSettledSnapshot(turn2Combined)) {
    anomalies.push({ id: "turn2-settled-still-active", message: "Ghostty turn2-settled snapshot still shows an active response state." })
  }
  if (shrinkCombined.includes("Live Shell compact view")) {
    anomalies.push({ id: "compact-transcript-fallback", message: "Ghostty preserved-scrollback path leaked compact transcript fallback during width-shrink scenario." })
  }

  const duplicateNeedles = ["Write a markdown answer", "# Streaming", "## Streaming", "BreadBoard · Claude Code", "Cooked for"]
  for (const needle of duplicateNeedles) {
    const visibleCount = countNeedle(shrink, needle)
    if (visibleCount > 1) {
      anomalies.push({
        id: duplicateId("visible", needle),
        message: `Ghostty visible pane duplicated '${needle}' ${visibleCount} times after width shrink.`,
      })
    }
    const scrollbackCount = countNeedle(shrinkScrollback, needle)
    if (scrollbackCount > 1) {
      anomalies.push({
        id: duplicateId("scrollback", needle),
        message: `Ghostty scrollback history duplicated '${needle}' ${scrollbackCount} times after width shrink.`,
      })
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateScrollbackGhosttyVisibleRegionBlastRadius(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
