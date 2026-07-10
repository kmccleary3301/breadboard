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

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const parseNdjson = async (filePath: string): Promise<Array<Record<string, unknown>>> => {
  const raw = await readText(filePath)
  return raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as Record<string, unknown>)
}

const countMatches = (text: string, pattern: RegExp): number => (text.match(pattern) ?? []).length

export const evaluateLiveWrapperEmulatorStreamingResizeChurn = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const labels = [
    "streaming-churn-0",
    "streaming-churn-1",
    "streaming-churn-2",
    "streaming-churn-3",
    "streaming-churn-4",
    "streaming-churn-5",
  ]

  const frames = await Promise.all(
    labels.map(async (label) => ({ label, body: await readText(path.join(caseDir, "observer_text", `${label}.txt`)) })),
  )
  const settled = await readText(path.join(caseDir, "observer_text", "settled-history.txt"))
  const viewportResets = await parseNdjson(path.join(caseDir, "viewport_resets.ndjson"))

  let finalItemObserved = false

  for (const frame of frames) {
    if (!frame.body.trim()) {
      anomalies.push({ id: `blank-${frame.label}`, message: `Streaming emulator frame ${frame.label} was blank.` })
      continue
    }
    finalItemObserved ||= frame.body.includes("- item-20")
    if (!frame.body.includes("❯") && !frame.body.includes("[ready]")) {
      anomalies.push({ id: `missing-composer-${frame.label}`, message: `Streaming emulator frame ${frame.label} lost the visible bottom-band boundary.` })
    }
    if (countMatches(frame.body, /item-/g) < 1) {
      anomalies.push({ id: `missing-stream-content-${frame.label}`, message: `Streaming emulator frame ${frame.label} did not show any streamed bullet content.` })
    }
  }

  finalItemObserved ||= settled.includes("- item-20")

  const pendingViewportResetKeys = viewportResets
    .slice(1)
    .filter((entry) => entry.pendingResponse === true)
  if (pendingViewportResetKeys.length > 1) {
    anomalies.push({
      id: "viewport-reset-churn",
      message: `Expected at most one pending-response viewport reset-key transition during emulator streaming resize churn, saw ${pendingViewportResetKeys.length}.`,
    })
  }

  if (!finalItemObserved) {
    anomalies.push({ id: "final-item-never-observed", message: 'Streaming emulator churn never captured the final long-answer line "- item-20".' })
  }
  const submittedPromptCount = countMatches(settled, /Answer with exactly this markdown shape/g)
  if (submittedPromptCount > 1) {
    anomalies.push({
      id: "settled-history-duplicate-prompt",
      message: `Settled emulator history duplicated the submitted prompt during resize churn; saw ${submittedPromptCount} copies.`,
    })
  }
  const tailCount = countMatches(settled, /- item-20/g)
  if (tailCount > 1) {
    anomalies.push({ id: "settled-history-duplicate-tail", message: `Settled emulator history duplicated the final long-answer tail; saw ${tailCount} copies of "- item-20".` })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperEmulatorStreamingResizeChurn(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
