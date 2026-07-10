import { promises as fs } from "node:fs"
import * as path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const sections = ["permissions-status", "visible-root-suggestions"] as const

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

const requireText = (anomalies: LayoutAnomaly[], label: string, body: string, needle: string) => {
  if (!body.includes(needle)) {
    anomalies.push({
      id: `${label}-missing-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`,
      message: `${label} does not include ${JSON.stringify(needle)}.`,
    })
  }
}

const readSnapshotsRaw = async (caseDir: string): Promise<string> => {
  const ptyPath = path.join(caseDir, "pty_snapshots.txt")
  const terminalPath = path.join(caseDir, "terminal_snapshots.txt")
  for (const filePath of [ptyPath, terminalPath]) {
    try {
      return await fs.readFile(filePath, "utf8")
    } catch {}
  }
  throw new Error(`No snapshot file found in ${caseDir}`)
}

export const evaluatePermissionsStatusSurface = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const raw = await readSnapshotsRaw(caseDir)
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []
  for (const label of sections) {
    if (!snapshots.has(label)) {
      anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
    }
  }

  const status = snapshots.get("permissions-status") ?? ""
  for (const needle of [
    "/permissions",
    "Permission status (read-only)",
    "Config:",
    "Launch permission mode: prompt",
    "Engine mode:",
    "Active approval: none",
    "Approval queue: 0",
    "Current approval scope: project",
    "Policy editing: not productized in this TUI yet.",
    "This command does not change permission policy.",
  ]) {
    requireText(anomalies, "permissions-status", status, needle)
  }

  const rootSnapshot = snapshots.get("visible-root-suggestions") ?? ""
  const rootMarker = rootSnapshot.lastIndexOf("❯ /")
  const root = rootMarker >= 0 ? rootSnapshot.slice(rootMarker) : rootSnapshot
  for (const hidden of ["/permissions", "/agents", "/diff", "/goal", "/fork"]) {
    if (root.includes(hidden)) {
      anomalies.push({ id: `root-suggests-${hidden.slice(1)}`, message: `${hidden} appeared in default visible root suggestions.` })
    }
  }

  const combined = [...snapshots.values()].join("\n")
  for (const needle of [
    "Mock assistant:",
    "TASK COMPLETE",
    "Tool execution results",
    "Unknown slash command",
    "Unknown command",
    "Status: feature-gated",
    "[responding]",
    "Assistant response",
  ]) {
    if (combined.includes(needle)) {
      anomalies.push({ id: `forbidden-${needle.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`, message: `Permission status output included forbidden marker ${JSON.stringify(needle)}.` })
    }
  }
  return anomalies
}

const run = async () => {
  const args = process.argv.slice(2)
  const caseDir = args[args.indexOf("--case-dir") + 1]
  if (!caseDir || args.indexOf("--case-dir") === -1) throw new Error("--case-dir is required")
  const anomalies = await evaluatePermissionsStatusSurface(path.resolve(caseDir))
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
