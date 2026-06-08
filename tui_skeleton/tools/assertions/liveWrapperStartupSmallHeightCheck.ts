import { promises as fs } from "node:fs"
import path from "node:path"
import { parseSnapshots } from "./liveWrapperLandingPersistenceCheck.ts"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const parseSurfaceRecords = (raw: string): Array<Record<string, unknown>> =>
  raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as Record<string, unknown>)

export const evaluateLiveWrapperStartupSmallHeight = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [snapshotsRaw, surfaceRaw] = await Promise.all([
    fs.readFile(path.join(caseDir, "pty_snapshots.txt"), "utf8"),
    fs.readFile(path.join(caseDir, "surface_model.ndjson"), "utf8"),
  ])
  const snapshots = parseSnapshots(snapshotsRaw)
  const startup = snapshots.find((entry) => entry.label === "startup-smallheight") ?? null
  if (!startup) {
    anomalies.push({ id: "missing-startup-snapshot", message: "Missing required snapshot \"startup-smallheight\"." })
    return anomalies
  }

  if (!startup.body.includes("[ready]")) {
    anomalies.push({ id: "startup-not-ready", message: "Constrained-height startup snapshot did not reach the ready footer." })
  }
  if (!startup.body.includes("BreadBoard · Claude Code") && !startup.body.includes("BreadBoard v")) {
    anomalies.push({ id: "orientation-header-missing", message: "Constrained-height startup did not render a BreadBoard orientation header." })
  }
  if (!startup.body.includes("❯ Try \"refactor <filepath>\"")) {
    anomalies.push({ id: "prompt-placeholder-missing", message: "Constrained-height startup did not retain the prompt placeholder surface." })
  }
  if (startup.body.includes("Tips for getting started")) {
    anomalies.push({ id: "board-landing-still-visible", message: "Constrained-height startup still rendered the board-only marker \"Tips for getting started\"." })
  }

  const surfaceRecords = parseSurfaceRecords(surfaceRaw)
  const latest = surfaceRecords[surfaceRecords.length - 1] ?? null
  if (!latest) {
    anomalies.push({ id: "missing-surface-record", message: "No surface-model record captured for constrained-height startup." })
  } else {
    if (latest["pendingResponse"] !== false) {
      anomalies.push({ id: "startup-pending", message: "Constrained-height startup should be idle, but the latest surface-model record is pending." })
    }
    if (latest["landingVariant"] !== "compact" && latest["landingVariant"] !== "split") {
      anomalies.push({
        id: "startup-variant-unexpected",
        message: `Expected constrained-height startup to resolve to compact orientation or a height-fit split landing, saw ${String(latest["landingVariant"])}.`
      })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperStartupSmallHeight(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
