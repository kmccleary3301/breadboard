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
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_resize_storm_modal.after_resize_patch2.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const sections = parseSections(raw)
  if (sections.length === 0) {
    throw new Error(`No snapshot sections found in ${resolved}`)
  }

  const failures: string[] = []
  for (const section of sections) {
    const counts = {
      header: countMatches(section.body, /BreadBoard v0\.0\.0a/g),
      tips: countMatches(section.body, /Tips for getting started/g),
      greeting: countMatches(section.body, /Hello again Querylake Manager/g),
      recentActivity: countMatches(section.body, /Recent activity/g),
      config: countMatches(section.body, /Config:/g),
      promptRule: countMatches(section.body, /Try "refactor <filepath>"/g),
    }
    if (counts.header > 1) {
      failures.push(`[${section.label}] duplicated header marker (${counts.header})`)
    }
    if (counts.tips > 1) {
      failures.push(`[${section.label}] duplicated tips block marker (${counts.tips})`)
    }
    if (counts.greeting > 1) {
      failures.push(`[${section.label}] duplicated landing greeting (${counts.greeting})`)
    }
    if (counts.recentActivity > 1) {
      failures.push(`[${section.label}] duplicated landing activity block (${counts.recentActivity})`)
    }
    if (counts.config > 1) {
      failures.push(`[${section.label}] duplicated config marker (${counts.config})`)
    }
    if (counts.promptRule > 1) {
      failures.push(`[${section.label}] duplicated prompt rule (${counts.promptRule})`)
    }
  }

  if (failures.length > 0) {
    throw new Error(`Resize artifact gate failed:\n${failures.join("\n")}`)
  }
  console.log(`[qc] resize artifact gate passed (${sections.length} snapshot section${sections.length === 1 ? "" : "s"})`)
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})

