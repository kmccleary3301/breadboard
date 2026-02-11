import { promises as fs } from "node:fs"
import path from "node:path"
import { normalizeSessionEvent } from "../src/repl/transcript/normalizeSessionEvent.js"
import type { SessionEvent } from "../src/api/types.js"

type CliOptions = {
  inputPath: string
  outputPath: string
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let inputPath = ""
  let outputPath = ""
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    if (arg === "--in") {
      inputPath = args[++i] ?? ""
    } else if (arg === "--out") {
      outputPath = args[++i] ?? ""
    }
  }
  if (!inputPath) {
    throw new Error("Usage: tsx scripts/normalize_events_jsonl.ts --in <events.jsonl> [--out <file>]")
  }
  if (!outputPath) {
    outputPath = inputPath.replace(/\.jsonl$/i, ".normalized.jsonl")
  }
  return { inputPath, outputPath }
}

const parseEvent = (raw: string): SessionEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return parsed.event as SessionEvent
    if (parsed.data && typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return inner as SessionEvent
      } catch {
        return null
      }
    }
    if (parsed.type) return parsed as SessionEvent
  }
  return null
}

const main = async () => {
  const options = parseArgs()
  const raw = await fs.readFile(options.inputPath, "utf8")
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  const normalizedLines: string[] = []
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    const normalized = normalizeSessionEvent(event)
    if (!normalized) continue
    normalizedLines.push(JSON.stringify(normalized))
  }
  await fs.writeFile(options.outputPath, normalizedLines.join("\n"), "utf8")
  console.log(`[normalize_events_jsonl] wrote ${options.outputPath}`)
}

main().catch((error) => {
  console.error("[normalize_events_jsonl] failed:", error)
  process.exit(1)
})

