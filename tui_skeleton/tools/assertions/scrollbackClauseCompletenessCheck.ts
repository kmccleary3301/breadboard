import { promises as fs } from "node:fs"
import path from "node:path"

export interface ClauseCompletenessFinding {
  readonly caseId: string
  readonly id: string
  readonly message: string
  readonly blocking: boolean
}

export interface ClauseCompletenessReport {
  readonly schemaVersion: 1
  readonly root: string
  readonly verdict: "pass" | "fail"
  readonly cases: number
  readonly findings: ClauseCompletenessFinding[]
}

interface ClauseVerdictsFile {
  readonly caseId?: string
  readonly clausesEvaluated?: unknown
  readonly verdicts?: unknown
}

const parseArgs = (): { root: string } => {
  const args = process.argv.slice(2)
  let root: string | undefined
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--root") root = args[++index]
  }
  if (!root) throw new Error("--root is required")
  return { root: path.resolve(root) }
}

const exists = async (file: string): Promise<boolean> => {
  try {
    await fs.access(file)
    return true
  } catch {
    return false
  }
}

const isCaseDir = async (dir: string): Promise<boolean> => exists(path.join(dir, "scrollback_clause_verdicts.json"))

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

const readClauseFile = async (caseDir: string): Promise<ClauseVerdictsFile | undefined> => {
  const text = await fs.readFile(path.join(caseDir, "scrollback_clause_verdicts.json"), "utf8")
  return JSON.parse(text) as ClauseVerdictsFile
}

const add = (findings: ClauseCompletenessFinding[], caseId: string, id: string, message: string): void => {
  findings.push({ caseId, id, message, blocking: true })
}

export const evaluateScrollbackClauseCompleteness = async (root: string): Promise<ClauseCompletenessReport> => {
  const absoluteRoot = path.resolve(root)
  const caseDirs = await resolveCaseDirs(absoluteRoot)
  const findings: ClauseCompletenessFinding[] = []

  if (caseDirs.length === 0) {
    findings.push({ caseId: path.basename(absoluteRoot), id: "clause-completeness-no-cases", message: "No case directories with scrollback_clause_verdicts.json were found.", blocking: true })
  }

  for (const caseDir of caseDirs) {
    const caseId = path.basename(caseDir)
    let data: ClauseVerdictsFile | undefined
    try {
      data = await readClauseFile(caseDir)
    } catch (error) {
      add(findings, caseId, "clause-completeness-unreadable", `Could not read or parse scrollback_clause_verdicts.json: ${(error as Error).message}`)
      continue
    }

    if (data.caseId && data.caseId !== caseId) {
      add(findings, caseId, "clause-completeness-case-id-mismatch", `Clause verdict caseId ${data.caseId} does not match directory ${caseId}.`)
    }

    const clausesEvaluated = Array.isArray(data.clausesEvaluated) ? data.clausesEvaluated : undefined
    const verdicts = Array.isArray(data.verdicts) ? data.verdicts : undefined

    if (!clausesEvaluated) {
      add(findings, caseId, "clause-completeness-missing-clauses", "clausesEvaluated is missing or not an array.")
    } else if (clausesEvaluated.length === 0) {
      add(findings, caseId, "clause-completeness-empty-clauses", "clausesEvaluated is empty; release-blocking P11 cases must be clause-complete.")
    }

    if (!verdicts) {
      add(findings, caseId, "clause-completeness-missing-verdicts", "verdicts is missing or not an array.")
    } else if (verdicts.length === 0) {
      add(findings, caseId, "clause-completeness-empty-verdicts", "verdicts is empty; release-blocking P11 cases must emit verdicts.")
    }

    if (clausesEvaluated && verdicts && clausesEvaluated.length !== verdicts.length) {
      add(findings, caseId, "clause-completeness-count-mismatch", `clausesEvaluated has ${clausesEvaluated.length} entries but verdicts has ${verdicts.length}.`)
    }

    for (const [index, verdict] of (verdicts ?? []).entries()) {
      if (typeof verdict !== "object" || verdict == null) {
        add(findings, caseId, "clause-completeness-invalid-verdict", `verdicts[${index}] is not an object.`)
        continue
      }
      const record = verdict as Record<string, unknown>
      if (typeof record.id !== "string" || !record.id) add(findings, caseId, "clause-completeness-verdict-id-missing", `verdicts[${index}] is missing an id.`)
      if (record.status !== "pass" && record.status !== "fail" && record.status !== "warn") add(findings, caseId, "clause-completeness-verdict-status-invalid", `verdicts[${index}] has invalid status ${String(record.status)}.`)
      if (typeof record.blocking !== "boolean") add(findings, caseId, "clause-completeness-verdict-blocking-missing", `verdicts[${index}] is missing boolean blocking field.`)
    }
  }

  return {
    schemaVersion: 1,
    root: absoluteRoot,
    verdict: findings.length === 0 ? "pass" : "fail",
    cases: caseDirs.length,
    findings,
  }
}

const run = async () => {
  const { root } = parseArgs()
  const report = await evaluateScrollbackClauseCompleteness(root)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (report.verdict === "fail") process.exit(1)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
