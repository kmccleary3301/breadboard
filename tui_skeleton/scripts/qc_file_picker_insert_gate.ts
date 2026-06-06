import { promises as fs } from "node:fs"

const target = process.argv[2] ?? "scripts/_tmp_qc_file_picker_insert.txt"

const fail = (message: string): never => {
  throw new Error(`[qc:file-picker-insert] ${message}`)
}

const run = async () => {
  const raw = await fs.readFile(target, "utf8")
  const expectedBasename = "LineEditor"
  const hasQueued = raw.includes("Files queued (1)")
  const hasMention = /❯\s+@?["']?(?:src|dist)\/repl\/components\/LineEditor\.(?:tsx|js)["']?/m.test(raw)
  const hasFile = /(?:src|dist)\/repl\/components\/LineEditor\.(?:tsx|js)/.test(raw)
  const hasError = /\b(error|failed)\b/i.test(raw)

  if (!hasQueued && !hasMention) {
    fail(`missing selected file marker (expected "Files queued (1)" or prompt mention containing "${expectedBasename}")`)
  }
  if (!hasFile) fail(`missing selected file path containing "${expectedBasename}"`)
  if (hasError) fail("unexpected error marker in snapshot")

  console.log("[qc:file-picker-insert] passed")
}

run().catch((error) => {
  console.error((error as Error).message)
  process.exitCode = 1
})
