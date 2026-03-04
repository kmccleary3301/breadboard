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
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_disconnected_resize_churn.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const sections = parseSections(raw)
  if (sections.length === 0) {
    throw new Error(`No snapshot sections found in ${resolved}`)
  }

  const failures: string[] = []
  for (const section of sections) {
    const promptRules = countMatches(section.body, /Try "refactor <filepath>"/g)
    const loadingBanners = countMatches(section.body, /Loading model catalog/g)
    const selectModelRows = countMatches(section.body, /Select model/g)
    if (promptRules > 2) {
      failures.push(`[${section.label}] excessive prompt-rule duplication (${promptRules})`)
    }
    if (loadingBanners > 2) {
      failures.push(`[${section.label}] excessive loading-banner duplication (${loadingBanners})`)
    }
    if (selectModelRows > 2) {
      failures.push(`[${section.label}] excessive model-picker duplication (${selectModelRows})`)
    }
  }

  if (failures.length > 0) {
    throw new Error(`Disconnected resize gate failed:\n${failures.join("\n")}`)
  }
  console.log(`[qc] disconnected resize gate passed (${sections.length} snapshot section${sections.length === 1 ? "" : "s"})`)
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
