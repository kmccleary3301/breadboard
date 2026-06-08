import { evaluateStatusVocabulary } from "./statusVocabularyCheck.ts"
import { evaluateHiddenEntryAffordance } from "./hiddenEntryAffordanceCheck.ts"
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { evaluateUiChromeBanlist } from "./uiChromeBanlistCheck.ts"

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

const readJson = async (file: string): Promise<any> => JSON.parse(await fs.readFile(file, 'utf8'))
const readText = async (file: string): Promise<string> => fs.readFile(file, 'utf8')

export const evaluateScrollbackHostHistoryPreservation = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  anomalies.push(...await evaluateHiddenEntryAffordance(caseDir))
  anomalies.push(...await evaluateStatusVocabulary(caseDir))
  anomalies.push(...await evaluateUiChromeBanlist(caseDir))
  const anchor = await readJson(path.join(caseDir, 'app_start_anchor.txt'))
  const boundsLines = (await readText(path.join(caseDir, 'managed_region_bounds.ndjson'))).split(/\r?\n/).filter(Boolean)
  const clauseVerdicts = await readJson(path.join(caseDir, 'scrollback_clause_verdicts.json'))
  const scrollbackFinal = await readText(path.join(caseDir, 'terminal_text', 'scrollback_final.txt'))

  if (anchor.mode !== 'preserved-scrollback') {
    anomalies.push({ id: 'anchor-mode-mismatch', message: `App-start anchor mode is ${String(anchor.mode)}, expected preserved-scrollback.` })
  }
  if (anchor.preAppHistoryPolicy !== 'untouched') {
    anomalies.push({ id: 'anchor-history-policy-mismatch', message: 'App-start anchor does not declare untouched pre-app history.' })
  }
  if (boundsLines.length === 0) {
    anomalies.push({ id: 'managed-region-bounds-missing', message: 'managed_region_bounds.ndjson did not receive any records.' })
  }
  if (!Array.isArray(clauseVerdicts.verdicts)) {
    anomalies.push({ id: 'clause-verdict-schema-missing', message: 'scrollback_clause_verdicts.json does not expose a verdicts array.' })
  }
  if (!scrollbackFinal.includes('PRE_APP_ALPHA') || !scrollbackFinal.includes('PRE_APP_BETA')) {
    anomalies.push({ id: 'pre-app-history-missing', message: 'Final scrollback text does not retain the seeded pre-app history lines.' })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateScrollbackHostHistoryPreservation(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + '\n')
}

if (import.meta.url === new URL(process.argv[1] ?? '', 'file:').href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
