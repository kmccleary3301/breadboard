import { promises as fs } from "node:fs"
import path from "node:path"

interface ClipboardDiffEntry {
  readonly caseId: string
  readonly diffFile: string
  readonly changes: Record<string, { previous: unknown; current: unknown }>
}

export const buildClipboardDiffReport = async (batchDir: string): Promise<string | null> => {
  const diffsDir = path.join(batchDir, "clipboard_diffs")
  const reportPath = path.join(batchDir, "clipboard_diff_report.txt")
  let entries: ClipboardDiffEntry[] = []
  try {
    const files = await fs.readdir(diffsDir)
    for (const file of files) {
      if (!file.endsWith(".json")) continue
      const filePath = path.join(diffsDir, file)
      try {
        const contents = await fs.readFile(filePath, "utf8")
        const parsed = JSON.parse(contents) as ClipboardDiffEntry
        if (parsed && typeof parsed.caseId === "string") {
          entries.push(parsed)
        }
      } catch {
        // ignore malformed entries
      }
    }
  } catch {
    entries = []
  }

  const lines: string[] = []
  lines.push("# Clipboard Diff Report")
  if (!entries.length) {
    lines.push("No clipboard metadata differences detected.")
  } else {
    for (const entry of entries) {
      lines.push("")
      lines.push(`Case: ${entry.caseId}`)
      lines.push(`Diff file: ${entry.diffFile}`)
      const keys = Object.keys(entry.changes)
      for (const key of keys) {
        const change = entry.changes[key]
        lines.push(`  - ${key}: ${JSON.stringify(change.previous)} -> ${JSON.stringify(change.current)}`)
      }
    }
  }
  lines.push("")
  await fs.writeFile(reportPath, lines.join("\n"), "utf8")
  return reportPath
}

