import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface ShellGeometryRecord {
  readonly event?: unknown
  readonly scrollbackMode?: unknown
  readonly rowSource?: unknown
  readonly resolvedRows?: unknown
  readonly stdoutRows?: unknown
  readonly processRows?: unknown
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const readJsonLines = async (file: string): Promise<ShellGeometryRecord[]> => {
  const raw = await fs.readFile(file, "utf8").catch(() => "")
  if (!raw.trim()) return []
  const records: ShellGeometryRecord[] = []
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue
    try {
      const parsed = JSON.parse(line) as ShellGeometryRecord
      if (parsed.event === "shell_geometry") records.push(parsed)
    } catch {
      records.push({ event: "shell_geometry", rowSource: "invalid-json" })
    }
  }
  return records
}

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null

export const evaluateScrollbackGeometry = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const records = await readJsonLines(path.join(caseDir, "render_timeline.ndjson"))
  const anomalies: LayoutAnomaly[] = []
  if (records.length === 0) {
    anomalies.push({
      id: "shell-geometry-missing",
      message: "No shell_geometry records were emitted; row-source provenance cannot be verified.",
    })
    return anomalies
  }

  for (const [index, record] of records.entries()) {
    if (record.rowSource !== "actual") {
      anomalies.push({
        id: "shell-geometry-row-source-not-actual",
        message: `shell_geometry record ${index} used rowSource=${String(record.rowSource)}; real-terminal scrollback lanes must use actual terminal rows.`,
      })
    }
    const resolvedRows = asNumber(record.resolvedRows)
    if (resolvedRows == null || resolvedRows <= 0) {
      anomalies.push({
        id: "shell-geometry-resolved-rows-invalid",
        message: `shell_geometry record ${index} did not report a positive resolvedRows value.`,
      })
    } else if (resolvedRows > 200) {
      anomalies.push({
        id: "shell-geometry-virtual-height",
        message: `shell_geometry record ${index} resolved ${resolvedRows} rows, which indicates virtual-height layout rather than the actual viewport.`,
      })
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateScrollbackGeometry(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
