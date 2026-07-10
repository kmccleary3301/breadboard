import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
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

const readJson = async <T>(filePath: string): Promise<T> => JSON.parse(await fs.readFile(filePath, "utf8")) as T

const readNdjson = async (filePath: string): Promise<Record<string, unknown>[]> => {
  const text = await fs.readFile(filePath, "utf8")
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Record<string, unknown>)
}

const expectStringField = (
  anomalies: LayoutAnomaly[],
  streamName: string,
  index: number,
  record: Record<string, unknown>,
  field: string,
) => {
  if (typeof record[field] !== "string" || (record[field] as string).trim() === "") {
    anomalies.push({
      id: `debug-stream-${streamName}-${field}-missing-${index}`,
      message: `${streamName} record ${index} is missing string field ${field}`,
    })
    return false
  }
  return true
}

const expectNumberField = (
  anomalies: LayoutAnomaly[],
  streamName: string,
  index: number,
  record: Record<string, unknown>,
  field: string,
) => {
  if (typeof record[field] !== "number") {
    anomalies.push({
      id: `debug-stream-${streamName}-${field}-missing-${index}`,
      message: `${streamName} record ${index} is missing numeric field ${field}`,
    })
    return false
  }
  return true
}

const expectBooleanField = (
  anomalies: LayoutAnomaly[],
  streamName: string,
  index: number,
  record: Record<string, unknown>,
  field: string,
) => {
  if (typeof record[field] !== "boolean") {
    anomalies.push({
      id: `debug-stream-${streamName}-${field}-missing-${index}`,
      message: `${streamName} record ${index} is missing boolean field ${field}`,
    })
    return false
  }
  return true
}

const expectStreamSchema = (
  anomalies: LayoutAnomaly[],
  streamName: string,
  index: number,
  record: Record<string, unknown>,
) => {
  expectNumberField(anomalies, streamName, index, record, "ts")
  expectStringField(anomalies, streamName, index, record, "iso")
  expectStringField(anomalies, streamName, index, record, "event")
  expectStringField(anomalies, streamName, index, record, "sessionId")

  if (streamName === "viewport_resets") {
    expectStringField(anomalies, streamName, index, record, "resetKey")
    return
  }

  if (streamName === "surface_model") {
    expectStringField(anomalies, streamName, index, record, "landingVariant")
    expectBooleanField(anomalies, streamName, index, record, "pendingResponse")
    expectNumberField(anomalies, streamName, index, record, "transcriptCommittedCount")
    expectNumberField(anomalies, streamName, index, record, "transcriptTailCount")
    return
  }

  if (streamName === "scrollback_feed" && record.event === "feed_reset") {
    expectBooleanField(anomalies, streamName, index, record, "enabled")
  }
}

const expectCorrelation = (
  anomalies: LayoutAnomaly[],
  streamName: string,
  records: Record<string, unknown>[],
  qcBatchId: string,
  qcCaseId: string,
) => {
  if (records.length === 0) {
    anomalies.push({ id: `debug-stream-${streamName}-empty`, message: `${streamName} is empty` })
    return
  }
  for (const [index, record] of records.entries()) {
    expectStreamSchema(anomalies, streamName, index, record)
    if (record.qcBatchId !== qcBatchId) {
      anomalies.push({
        id: `debug-stream-${streamName}-batch-mismatch-${index}`,
        message: `${streamName} record ${index} missing qcBatchId=${qcBatchId}`,
      })
      return
    }
    if (record.qcCaseId !== qcCaseId) {
      anomalies.push({
        id: `debug-stream-${streamName}-case-mismatch-${index}`,
        message: `${streamName} record ${index} missing qcCaseId=${qcCaseId}`,
      })
      return
    }
  }
}

export const evaluateDebugStreamCorrelation = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const config = await readJson<{ env?: Record<string, string> }>(path.join(caseDir, "config.json"))
  const qcBatchId = config.env?.BREADBOARD_QC_BATCH_ID ?? ""
  const qcCaseId = config.env?.BREADBOARD_QC_CASE_ID ?? ""
  const anomalies: LayoutAnomaly[] = []

  if (!qcBatchId || !qcCaseId) {
    return [{ id: "debug-stream-correlation-config-missing", message: "config.json is missing BREADBOARD_QC_BATCH_ID or BREADBOARD_QC_CASE_ID" }]
  }

  const streamFiles = [
    ["viewport_resets", "viewport_resets.ndjson"],
    ["surface_model", "surface_model.ndjson"],
    ["scrollback_feed", "scrollback_feed.ndjson"],
  ] as const

  for (const [streamName, fileName] of streamFiles) {
    const filePath = path.join(caseDir, fileName)
    const records = await readNdjson(filePath)
    expectCorrelation(anomalies, streamName, records, qcBatchId, qcCaseId)
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateDebugStreamCorrelation(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
