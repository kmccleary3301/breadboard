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

export const evaluateOwnedLiveHeightChange = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const startup = await readObserverText(caseDir, "startup.txt")
  const streamingMid = await readObserverText(caseDir, "streaming-mid.txt")
  const afterHeightShrink = await readObserverText(caseDir, "after-height-shrink.txt")
  const settled = await readObserverText(caseDir, "settled-after-height-shrink.txt")

  if (!startup.includes("owned live shell")) {
    anomalies.push({ id: "owned-live-host-missing", message: "Startup view does not expose the owned live shell host label." })
  }

  if (!streamingMid.includes("[responding]") || !streamingMid.includes("#") && !streamingMid.includes("## Streaming")) {
    anomalies.push({ id: "streaming-mid-missing", message: "Streaming checkpoint does not show active owned-live streaming content." })
  }

  const afterHeightShrinkNonblank = countNonblank(afterHeightShrink)
  if (afterHeightShrinkNonblank < 8) {
    anomalies.push({ id: "after-height-shrink-near-blank", message: `Height-shrink checkpoint is near-blank (${afterHeightShrinkNonblank} nonblank lines).` })
  }

  if (!afterHeightShrink.includes("owned live shell")) {
    anomalies.push({ id: "after-height-shrink-host-missing", message: "Height-shrink checkpoint lost the owned live shell host label." })
  }

  const settledHasExpectedBody = settled.includes("# Streaming") && (
    settled.includes("Final.") ||
    settled.includes("console.log('ghostty')") ||
    settled.includes("│ mode │ slow")
  )
  if (!settledHasExpectedBody) {
    anomalies.push({ id: "settled-output-missing", message: "Settled post-height-shrink view lost the expected assistant body content." })
  }

  if (!settled.includes("[ready]") && !settled.includes("enter send")) {
    anomalies.push({ id: "settled-ready-cue-missing", message: "Settled post-height-shrink view does not show the ready-state cue." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateOwnedLiveHeightChange(caseDir)
  process.stdout.write(JSON.stringify(anomalies, null, 2) + "\n")
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
