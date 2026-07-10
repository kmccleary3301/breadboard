import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { promises as fs } from 'node:fs'
import path from 'node:path'
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
    if (args[i] === '--case-dir') caseDir = args[++i]
  }
  if (!caseDir) throw new Error('--case-dir is required')
  return { caseDir: path.resolve(caseDir) }
}

const readObserverText = async (caseDir: string, name: string): Promise<string> =>
  fs.readFile(path.join(caseDir, 'observer_text', name), 'utf8')

const readOptionalText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, 'utf8')
  } catch {
    return ''
  }
}

const countNonblank = (value: string): number => value.split(/\r?\n/).filter((line) => line.trim().length > 0).length
const countNeedle = (text: string, needle: string): number => text.split(needle).length - 1

const duplicateId = (scope: 'visible' | 'scrollback', needle: string): string =>
  `after-shrink-${scope}-duplicate-${needle.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`

export const evaluateScrollbackVisibleRegionBlastRadius = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  anomalies.push(...await evaluateVisibleComposition(caseDir))
  anomalies.push(...await evaluateStartupLandingGap(caseDir))
  anomalies.push(...await evaluateLandingLifecycle(caseDir))
  anomalies.push(...await evaluateOverlayFootprint(caseDir))
  const turn1 = await readObserverText(caseDir, 'turn1-settled.txt')
  const afterShrink = await readObserverText(caseDir, 'after-width-shrink.txt')
  const turn2 = await readObserverText(caseDir, 'turn2-settled.txt')
  const scrollbackFinal = await readOptionalText(path.join(caseDir, 'terminal_text', 'scrollback_final.txt'))

  if (countNonblank(afterShrink) < 8) {
    anomalies.push({ id: 'after-shrink-visible-near-blank', message: 'Visible managed region collapsed to a near-blank frame after width shrink.' })
  }
  if (!afterShrink.includes('hello from wezterm turn one')) {
    anomalies.push({ id: 'after-shrink-visible-turn-context-missing', message: 'Visible region lost the first-turn context after width shrink.' })
  }
  if (!turn2.includes('hello from wezterm turn two')) {
    anomalies.push({ id: 'turn2-context-missing', message: 'Settled second-turn view does not include the second prompt context.' })
  }

  const duplicateNeedles = ['Show me some markdown.', 'hello from wezterm turn one', 'hello from wezterm turn two', 'BreadBoard · Claude Code', 'Cooked for']
  for (const needle of duplicateNeedles) {
    const visibleCount = countNeedle(afterShrink, needle)
    if (visibleCount > 1) {
      anomalies.push({
        id: duplicateId('visible', needle),
        message: `Width-shrink visible checkpoint duplicated '${needle}' ${visibleCount} times.`,
      })
    }
    const scrollbackCount = countNeedle(scrollbackFinal, needle)
    if (scrollbackCount > 1) {
      anomalies.push({
        id: duplicateId('scrollback', needle),
        message: `Width-shrink scrollback history duplicated '${needle}' ${scrollbackCount} times.`,
      })
    }
  }

  if (turn1.includes('... ') && turn1.includes(' earlier entries hidden')) {
    anomalies.push({ id: 'turn1-hidden-history-cue', message: 'Preserved scrollback turn-1 settled view still relies on hidden-history cueing.' })
  }
  if (turn2.includes('... ') && turn2.includes(' earlier entries hidden')) {
    anomalies.push({ id: 'turn2-hidden-history-cue', message: 'Preserved scrollback turn-2 settled view still relies on hidden-history cueing.' })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateScrollbackVisibleRegionBlastRadius(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + '\n')
}

if (import.meta.url === new URL(process.argv[1] ?? '', 'file:').href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
