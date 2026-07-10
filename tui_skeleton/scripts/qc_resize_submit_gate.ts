import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

interface SnapshotSection {
  readonly label: string
  readonly body: string
}

const parseSections = (text: string): SnapshotSection[] => {
  const lines = text.split(/\r?\n/)
  const sections: SnapshotSection[] = []
  let currentLabel = "snapshot"
  let currentBody: string[] = []
  const flush = () => {
    sections.push({ label: currentLabel, body: currentBody.join("\n") })
    currentBody = []
  }
  for (const line of lines) {
    if (line.startsWith("# ")) {
      if (currentBody.length > 0 || sections.length > 0) flush()
      currentLabel = line.slice(2).trim() || "snapshot"
      continue
    }
    currentBody.push(line)
  }
  flush()
  return sections.filter((section) => section.body.trim().length > 0)
}

const countMatches = (text: string, pattern: RegExp): number => {
  const matches = text.match(pattern)
  return matches ? matches.length : 0
}

const main = async () => {
  const arg = process.argv[2]
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_resize_submit_churn.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const sections = parseSections(raw)
  if (sections.length === 0) {
    throw new Error(`No snapshot sections found in ${resolved}`)
  }

  const failures: string[] = []
  for (const section of sections) {
    const promptLines = countMatches(section.body, /^\s*❯ resize churn qc\s*$/gm)
    const skillEchoes = countMatches(section.body, /^Skills selection updated\.$/gm)
    const placeholderLines = countMatches(section.body, /^❯ Try "refactor <filepath>"$/gm)
    const readyFooters = countMatches(section.body, /• \[ready\].*enter send/g)
    const respondingFooters = countMatches(section.body, /\[responding\].*esc interrupt/g)
    const errorHeaders = countMatches(section.body, /● \[error\]/g)

    if (promptLines !== 1) {
      failures.push(`[${section.label}] expected exactly one submitted prompt row, found ${promptLines}`)
    }
    if (skillEchoes > 1) {
      failures.push(`[${section.label}] duplicated transient skill-update rows (${skillEchoes})`)
    }
    if (respondingFooters > 0) {
      failures.push(`[${section.label}] snapshot was captured before the run settled back to ready`)
    }
    if (placeholderLines > 0 && readyFooters !== 1 && (promptLines > 0 || errorHeaders > 0)) {
      failures.push(`[${section.label}] idle placeholder leaked into a non-ready post-submit snapshot`)
    }
    if (readyFooters !== 1) {
      failures.push(`[${section.label}] expected exactly one ready footer, found ${readyFooters}`)
    }
  }

  if (failures.length > 0) {
    throw new Error(`Resize submit gate failed:\n${failures.join("\n")}`)
  }
  console.log(`[qc] resize submit gate passed (${sections.length} snapshot section${sections.length === 1 ? "" : "s"})`)
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
