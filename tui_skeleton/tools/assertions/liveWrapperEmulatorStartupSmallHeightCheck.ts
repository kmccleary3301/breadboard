import { promises as fs } from "node:fs"
import path from "node:path"

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

export const evaluateLiveWrapperEmulatorStartupSmallHeight = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [startupBody, surfaceRaw] = await Promise.all([
    fs.readFile(path.join(caseDir, "observer_text", "startup-smallheight.txt"), "utf8"),
    fs.readFile(path.join(caseDir, "surface_model.ndjson"), "utf8"),
  ])

  if (!startupBody.includes("[ready]")) {
    anomalies.push({ id: "startup-not-ready", message: "Constrained-height emulator startup did not reach the ready footer." })
  }
  if (!/❯\s+(?:Try "refactor <filepath>"|Type your request)/.test(startupBody)) {
    anomalies.push({ id: "prompt-placeholder-missing", message: "Constrained-height emulator startup did not retain the prompt placeholder surface." })
  }

  const hasBoard = startupBody.includes("Tips for getting started")
  const hasCompact = startupBody.includes("BreadBoard · Claude Code")
  const hasSplit = startupBody.includes("Using Config")

  if (!hasBoard && !hasCompact && !hasSplit) {
    anomalies.push({ id: "no-startup-surface", message: "Constrained-height emulator startup did not render any accepted landing/orientation surface." })
  }

  const surfaceRecords = parseSurfaceRecords(surfaceRaw)
  const latest = surfaceRecords[surfaceRecords.length - 1] ?? null
  if (!latest) {
    anomalies.push({ id: "missing-surface-record", message: "No surface-model record captured for constrained-height emulator startup." })
  } else {
    if (latest["pendingResponse"] !== false) {
      anomalies.push({ id: "startup-pending", message: "Constrained-height emulator startup should be idle, but the latest surface-model record is pending." })
    }
    const variant = String(latest["landingVariant"] ?? "")
    if (!["board", "split", "compact"].includes(variant)) {
      anomalies.push({ id: "startup-variant-invalid", message: `Expected constrained-height emulator startup to resolve to a known landing variant, saw ${variant || "<empty>"}.` })
    }
    if (variant === "board" && !hasBoard) {
      anomalies.push({ id: "board-variant-mismatch", message: "Surface model reported board startup, but the board landing marker was not visible." })
    }
    if (variant === "split" && !hasSplit) {
      anomalies.push({ id: "split-variant-mismatch", message: "Surface model reported split startup, but the split landing marker was not visible." })
    }
    if (variant === "compact" && !hasCompact) {
      anomalies.push({ id: "compact-variant-mismatch", message: "Surface model reported compact startup, but the compact orientation header was not visible." })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperEmulatorStartupSmallHeight(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
