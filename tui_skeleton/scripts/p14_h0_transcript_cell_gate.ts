import { promises as fs } from "node:fs"
import process from "node:process"

interface TranscriptCellRecord {
  readonly id?: string
  readonly role?: string
  readonly lifecycle?: string
  readonly source?: string
  readonly speaker?: string
  readonly dedupeKey?: string | null
  readonly textPreview?: string
  readonly renderModes?: readonly string[]
}

interface DumpRecord {
  readonly state?: {
    readonly transcriptCells?: readonly TranscriptCellRecord[]
  }
  readonly transcriptCells?: readonly TranscriptCellRecord[]
}

const readArgs = () => {
  const args = process.argv.slice(2)
  const stateDumpPath = args[0]
  if (!stateDumpPath) {
    throw new Error("Usage: tsx scripts/p14_h0_transcript_cell_gate.ts <repl_state.ndjson> [artifact.json]")
  }
  return { stateDumpPath, artifactPath: args[1] }
}

const readLatestCells = async (stateDumpPath: string): Promise<TranscriptCellRecord[]> => {
  const raw = await fs.readFile(stateDumpPath, "utf8")
  const records = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line) as DumpRecord)
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const record = records[index]
    const cells = record.state?.transcriptCells ?? record.transcriptCells
    if (Array.isArray(cells) && cells.length > 0) {
      return [...cells]
    }
  }
  throw new Error(`No transcriptCells found in ${stateDumpPath}`)
}

const assertCell = (
  cells: readonly TranscriptCellRecord[],
  predicate: (cell: TranscriptCellRecord) => boolean,
  message: string,
) => {
  if (!cells.some(predicate)) {
    throw new Error(message)
  }
}

const main = async () => {
  const { stateDumpPath, artifactPath } = readArgs()
  const cells = await readLatestCells(stateDumpPath)

  assertCell(
    cells,
    (cell) =>
      cell.role === "user-request" &&
      cell.speaker === "user" &&
      cell.lifecycle === "committed" &&
      String(cell.textPreview ?? "").includes("H0_CELL_OK"),
    "Expected a committed user-request transcript cell containing the submitted prompt.",
  )
  assertCell(
    cells,
    (cell) =>
      cell.role === "assistant-message" &&
      cell.speaker === "assistant" &&
      cell.lifecycle === "committed" &&
      String(cell.textPreview ?? "").includes("H0_CELL_OK"),
    "Expected a committed assistant-message transcript cell containing H0_CELL_OK.",
  )

  const statusKeys = new Set<string>()
  for (const cell of cells) {
    if (cell.role !== "status" || !cell.dedupeKey) continue
    if (statusKeys.has(cell.dedupeKey)) {
      throw new Error(`Duplicate status transcript cell dedupeKey detected: ${cell.dedupeKey}`)
    }
    statusKeys.add(cell.dedupeKey)
  }

  const missingModes = cells.filter((cell) => !cell.renderModes?.includes("rich") || !cell.renderModes.includes("raw") || !cell.renderModes.includes("viewer"))
  if (missingModes.length > 0) {
    throw new Error(`Transcript cells missing rich/raw/viewer render modes: ${missingModes.map((cell) => cell.id ?? "(unknown)").join(", ")}`)
  }

  if (artifactPath) {
    await fs.writeFile(artifactPath, `${JSON.stringify(cells, null, 2)}\n`, "utf8")
  }

  console.log(
    JSON.stringify(
      {
        verdict: "pass",
        cellCount: cells.length,
        roles: cells.map((cell) => cell.role),
        artifactPath: artifactPath ?? null,
      },
      null,
      2,
    ),
  )
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exit(1)
})
