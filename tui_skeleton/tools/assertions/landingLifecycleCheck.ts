import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly { readonly id: string; readonly message: string }

const VALID_STATES = new Set([
  "fresh-visible",
  "compact-visible",
  "committed-snapshot",
  "retired",
  "suppressed-small-height",
])

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const readJsonLines = async (file: string): Promise<Record<string, unknown>[]> => {
  const raw = await fs.readFile(file, "utf8").catch(() => "")
  if (!raw.trim()) return []
  const records: Record<string, unknown>[] = []
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue
    try {
      const parsed = JSON.parse(line)
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        records.push(parsed as Record<string, unknown>)
      }
    } catch {
      records.push({ event: "invalid-json" })
    }
  }
  return records
}

const readJsonFile = async (file: string): Promise<Record<string, unknown> | null> => {
  const raw = await fs.readFile(file, "utf8").catch(() => "")
  if (!raw.trim()) return null
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch {
    return { event: "invalid-json" }
  }
}

const asState = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value.trim() : null

const hasMeaningfulInteraction = (record: Record<string, unknown>): boolean => {
  const committed = typeof record.transcriptCommittedCount === "number" ? record.transcriptCommittedCount : 0
  const tail = typeof record.transcriptTailCount === "number" ? record.transcriptTailCount : 0
  return record.pendingResponse === true || committed > 0 || tail > 0
}

export const evaluateLandingLifecycle = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const anchor = await readJsonFile(path.join(caseDir, "app_start_anchor.txt"))
  const records = await readJsonLines(path.join(caseDir, "surface_model.ndjson"))
  const surfaceRecords = records.filter((record) => record.event === "surface_model")

  if (anchor) {
    const anchorState = asState(anchor.landingLifecycleState)
    if (!anchorState) {
      anomalies.push({ id: "landing-lifecycle-anchor-missing", message: "App-start anchor does not include landingLifecycleState." })
    } else if (!VALID_STATES.has(anchorState)) {
      anomalies.push({ id: "landing-lifecycle-anchor-invalid", message: `App-start anchor has invalid landingLifecycleState '${anchorState}'.` })
    }
  }

  if (surfaceRecords.length === 0) {
    anomalies.push({ id: "landing-lifecycle-surface-missing", message: "No surface_model records were emitted for landing lifecycle audit." })
    return anomalies
  }

  for (const [index, record] of surfaceRecords.entries()) {
    const state = asState(record.landingLifecycleState)
    const reason = typeof record.landingLifecycleReason === "string" ? record.landingLifecycleReason.trim() : ""
    if (!state) {
      anomalies.push({ id: "landing-lifecycle-state-missing", message: `surface_model record ${index} is missing landingLifecycleState.` })
      continue
    }
    if (!VALID_STATES.has(state)) {
      anomalies.push({ id: "landing-lifecycle-state-invalid", message: `surface_model record ${index} has invalid landingLifecycleState '${state}'.` })
    }
    if (!reason) {
      anomalies.push({ id: "landing-lifecycle-reason-missing", message: `surface_model record ${index} is missing landingLifecycleReason.` })
    }
    if ((state === "fresh-visible" || state === "compact-visible") && record.landingLifecycleVisibleInline !== true) {
      anomalies.push({ id: "landing-lifecycle-visible-flag-mismatch", message: `surface_model record ${index} is ${state} but not marked visible inline.` })
    }
    if (state === "committed-snapshot" && record.landingLifecycleCommittedSnapshot !== true) {
      anomalies.push({ id: "landing-lifecycle-snapshot-flag-mismatch", message: `surface_model record ${index} is committed-snapshot but not marked as a committed snapshot.` })
    }
  }

  const first = surfaceRecords[0]
  const firstState = asState(first.landingLifecycleState)
  if (!hasMeaningfulInteraction(first) && (firstState === "retired" || firstState === "suppressed-small-height")) {
    anomalies.push({
      id: "landing-lifecycle-startup-not-visible",
      message: `First surface_model record has startup landing state '${firstState}' before meaningful interaction.`,
    })
  }

  const committedSnapshot = surfaceRecords.some((record) => asState(record.landingLifecycleState) === "committed-snapshot")
  const retiredAfterInteraction = surfaceRecords.some((record) => asState(record.landingLifecycleState) === "retired" && hasMeaningfulInteraction(record))
  const visibleAfterInteraction = surfaceRecords.some((record) => {
    const state = asState(record.landingLifecycleState)
    return (state === "fresh-visible" || state === "compact-visible") && hasMeaningfulInteraction(record)
  })
  if (visibleAfterInteraction && !committedSnapshot && !retiredAfterInteraction) {
    anomalies.push({
      id: "landing-lifecycle-no-retirement-evidence",
      message: "Landing stayed visible after meaningful interaction without committed-snapshot or retired lifecycle evidence.",
    })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLandingLifecycle(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error))
    process.exit(1)
  })
}
