import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

interface WeztermEvent {
  readonly event?: string
  readonly cols?: number
  readonly rows?: number
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir }
}

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const parseEvent = (line: string): WeztermEvent => {
  const event: WeztermEvent = {}
  for (const field of line.split("\t")) {
    const [key, ...rest] = field.split("=")
    const value = rest.join("=")
    if (key === "event") event.event = value
    else if (key === "cols") event.cols = Number(value)
    else if (key === "rows") event.rows = Number(value)
  }
  return event
}

const readEvents = async (filePath: string): Promise<WeztermEvent[]> => {
  const raw = await readText(filePath)
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map(parseEvent)
}

const sawResize = (events: readonly WeztermEvent[], cols: number, rows: number) =>
  events.some((event) => event.event === "window-resized" && event.cols === cols && event.rows === rows)

export const evaluateMaintenanceWrapperEmulatorResize = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const events = await readEvents(path.join(caseDir, "observer_runtime", "wezterm-events.ndjson"))
  if (!sawResize(events, 90, 24)) {
    anomalies.push({ id: "emulator-missing-small-resize", message: "Expected WezTerm observer log to record a resize to 90x24." })
  }
  if (!sawResize(events, 120, 36)) {
    anomalies.push({ id: "emulator-missing-large-resize", message: "Expected WezTerm observer log to record a resize back to 120x36." })
  }
  const afterAnswer = await readText(path.join(caseDir, "observer_text", "after-answer.txt"))
  if (!afterAnswer.includes("Verification: verification receipt present")) {
    anomalies.push({ id: "emulator-resize-answer-missing", message: "Expected after-answer observer snapshot to include the mock C-filesystem verification receipt." })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateMaintenanceWrapperEmulatorResize(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
