import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const parseSections = (raw: string): Map<string, string> => {
  const lines = raw.split(/\r?\n/)
  const sections = new Map<string, string[]>()
  let current = ""
  for (const line of lines) {
    const match = /^#\s+(.+)$/.exec(line)
    if (match) {
      current = match[1].trim()
      if (!sections.has(current)) sections.set(current, [])
      continue
    }
    if (!current) continue
    sections.get(current)?.push(line)
  }
  return new Map(Array.from(sections.entries(), ([label, content]) => [label, content.join("\n")]))
}

const main = async () => {
  const arg = process.argv[2]
  const target = arg?.trim() ? arg.trim() : "scripts/_tmp_qc_command_modal_sweep.txt"
  const resolved = path.isAbsolute(target) ? target : path.join(process.cwd(), target)
  const raw = await fs.readFile(resolved, "utf8")
  const sections = parseSections(raw)
  const failures: string[] = []

  const expectContains = (label: string, token: string, message: string) => {
    const body = sections.get(label)
    if (!body) {
      failures.push(`missing snapshot section: ${label}`)
      return
    }
    if (!body.includes(token)) {
      failures.push(`${label}: ${message}`)
    }
  }
  const expectAnyContains = (label: string, tokens: string[], message: string) => {
    const body = sections.get(label)
    if (!body) {
      failures.push(`missing snapshot section: ${label}`)
      return
    }
    if (!tokens.some((token) => body.includes(token))) {
      failures.push(`${label}: ${message}`)
    }
  }

  expectContains("models-open", "Select model", "model picker did not open")
  expectContains("skills-open", "Skills", "skills modal did not open")
  expectContains("tasks-open", "Background tasks", "tasks modal did not open")
  expectContains("todos-open", "Todos", "todos modal did not open")
  expectContains("usage-open", "Usage", "usage modal did not open")
  expectAnyContains(
    "file-picker-open",
    ["src/", "Indexing…", "Indexing...", "Index truncated at"],
    "file picker did not open with @ query",
  )
  expectContains("after-close-all", "Try \"refactor <filepath>\"", "composer prompt missing after closing overlays")

  if (failures.length > 0) {
    throw new Error(`Command/modal sweep gate failed:\n${failures.join("\n")}`)
  }

  console.log("[qc] command/modal sweep gate passed")
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
