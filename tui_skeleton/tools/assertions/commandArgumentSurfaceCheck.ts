import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const sections = new Set(["invalid-mode-result", "missing-model-result", "extra-help-result"])

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
      if (sections.has(next)) {
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

export const evaluateCommandArgumentSurface = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const raw = await fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8")
  const snapshots = parseSnapshots(raw)
  const anomalies: LayoutAnomaly[] = []
  for (const label of sections) {
    if (!snapshots.has(label)) anomalies.push({ id: `missing-${label}`, message: `Missing ${label} snapshot.` })
  }
  if (anomalies.length > 0) return anomalies

  const invalidMode = snapshots.get("invalid-mode-result") ?? ""
  if (!invalidMode.includes("Invalid mode: chaos") || !invalidMode.includes("Expected: plan|build|auto")) {
    anomalies.push({ id: "invalid-mode-not-rejected", message: "Malformed /mode argument was not rejected with expected enum help." })
  }
  const missingModel = snapshots.get("missing-model-result") ?? ""
  if (!missingModel.includes("Missing required argument <id>") || !missingModel.includes("Usage: /model <id>")) {
    anomalies.push({ id: "missing-model-id-not-rejected", message: "Missing /model argument was not rejected with expected usage help." })
  }
  const extraHelp = snapshots.get("extra-help-result") ?? ""
  if (!extraHelp.includes("/help does not take arguments") || !extraHelp.includes("Usage: /help")) {
    anomalies.push({ id: "extra-help-arg-not-rejected", message: "Extra /help argument was not rejected before dispatch." })
  }
  const combined = [...snapshots.values()].join("\n")
  if (combined.includes("Mock assistant:") || combined.includes("[responding]")) {
    anomalies.push({ id: "malformed-command-submitted-to-model", message: "Malformed known slash command appears to have reached model submission." })
  }
  return anomalies
}

const run = async () => {
  const args = process.argv.slice(2)
  const caseDir = args[args.indexOf("--case-dir") + 1]
  if (!caseDir || args.indexOf("--case-dir") === -1) throw new Error("--case-dir is required")
  const anomalies = await evaluateCommandArgumentSurface(path.resolve(caseDir))
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
