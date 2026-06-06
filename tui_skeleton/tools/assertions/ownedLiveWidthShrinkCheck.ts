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

const readObserverText = async (caseDir: string, name: string): Promise<string> =>
  fs.readFile(path.join(caseDir, "observer_text", name), "utf8")

const countNonblank = (value: string): number => value.split(/\r?\n/).filter((line) => line.trim().length > 0).length

export const evaluateOwnedLiveWidthShrink = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const startup = await readObserverText(caseDir, "startup.txt")
  const afterShrink = await readObserverText(caseDir, "after-width-shrink.txt")
  const turn2 = await readObserverText(caseDir, "turn2-settled.txt")

  if (!startup.includes("owned live shell")) {
    anomalies.push({ id: "owned-live-host-missing", message: "Startup view does not expose the owned live shell host label." })
  }

  if (!afterShrink.includes("owned live shell")) {
    anomalies.push({ id: "after-shrink-host-missing", message: "Width-shrink checkpoint lost the owned live shell host label." })
  }

  const afterShrinkNonblank = countNonblank(afterShrink)
  if (afterShrinkNonblank < 8) {
    anomalies.push({ id: "after-shrink-near-blank", message: `Width-shrink checkpoint is still near-blank (${afterShrinkNonblank} nonblank lines).` })
  }

  if (!afterShrink.includes("hello from wezterm turn one")) {
    anomalies.push({ id: "after-shrink-turn-context-missing", message: "Width-shrink checkpoint lost the first-turn context entirely." })
  }

  if (!turn2.includes("hello from wezterm turn two")) {
    anomalies.push({ id: "turn2-context-missing", message: "Settled second-turn view does not show the second prompt context." })
  }

  if (!turn2.includes("Live Shell compact view")) {
    anomalies.push({ id: "compact-transcript-cue-missing", message: "Settled second-turn view does not expose the compact transcript cue." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateOwnedLiveWidthShrink(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
