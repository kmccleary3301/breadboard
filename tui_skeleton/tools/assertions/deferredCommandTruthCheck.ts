import { promises as fs } from "node:fs"
import * as path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const sections = [
  "deferred-goal",
  "deferred-fork",
  "visible-root-suggestions",
] as const

const required: Record<string, readonly string[]> = {
  "deferred-goal": ["/goal", "durable goal persistence", "Status: feature-gated"],
  "deferred-fork": ["/fork", "backend session graph", "Status: feature-gated"],
  "visible-root-suggestions": ["/resume", "/transcript", "/attach", "/models", "/shortcuts"],
}

const parseSnapshots = (raw: string): Map<string, string> => {
  const parsed = new Map<string, string>()
  let label: string | null = null
  let lines: string[] = []
  const flush = () => {
    if (label) parsed.set(label, lines.join("\n"))
  }
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith("# ")) {
      const next = line.slice(2).trim()
      if ((sections as readonly string[]).includes(next)) {
        flush()
        label = next
        lines = []
        continue
      }
    }
    if (label) lines.push(line)
  }
  flush()
  return parsed
}

export const evaluateDeferredCommandTruth = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const raw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []
  for (const label of sections) {
    const body = snapshots.get(label)
    if (!body) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
      continue
    }
    for (const needle of required[label]) {
      if (!body.includes(needle)) {
        anomalies.push({ id: `${label}-missing-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`, message: `${label} does not include ${JSON.stringify(needle)}.` })
      }
    }
  }

  const rootSnapshot = snapshots.get("visible-root-suggestions") ?? ""
  const rootMarker = rootSnapshot.lastIndexOf("❯ /")
  const root = rootMarker >= 0 ? rootSnapshot.slice(rootMarker) : rootSnapshot
  for (const command of ["/diff", "/permissions", "/agents", "/goal", "/fork"]) {
    if (root.includes(command)) {
      anomalies.push({ id: `root-suggests-${command.slice(1)}`, message: `${command} appeared in the default visible slash suggestions despite being feature-gated.` })
    }
  }

  const combined = [...snapshots.values()].join("\n")
  const forbidden = [
    "Mock assistant:",
    "TASK COMPLETE",
    "Tool execution results",
    "Unknown slash command",
    "Unknown command",
    "[responding]",
    "Assistant response",
  ]
  for (const needle of forbidden) {
    if (combined.includes(needle)) {
      anomalies.push({ id: `forbidden-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`, message: `Deferred command output included forbidden model/submission marker ${JSON.stringify(needle)}.` })
    }
  }
  return anomalies
}

const run = async () => {
  const args = process.argv.slice(2)
  const caseDir = args[args.indexOf("--case-dir") + 1]
  if (!caseDir || args.indexOf("--case-dir") === -1) throw new Error("--case-dir is required")
  const anomalies = await evaluateDeferredCommandTruth(path.resolve(caseDir))
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
