import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly { readonly id: string; readonly message: string }

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (!caseDir) throw new Error("--case-dir is required")
  return { caseDir: path.resolve(caseDir) }
}

const readTextFiles = async (caseDir: string): Promise<Array<{ file: string; body: string }>> => {
  const candidates: string[] = [
    path.join(caseDir, "terminal_snapshots.txt"),
    path.join(caseDir, "terminal_plain.txt"),
    path.join(caseDir, "pty_snapshots.txt"),
    path.join(caseDir, "terminal_text", "visible_final.txt"),
  ]
  const observerDir = path.join(caseDir, "observer_text")
  const observerEntries = await fs.readdir(observerDir).catch(() => [])
  for (const entry of observerEntries) {
    if (entry.endsWith(".txt") && !entry.endsWith(".x11.txt") && !entry.endsWith(".summary.txt")) {
      candidates.push(path.join(observerDir, entry))
    }
  }

  const out: Array<{ file: string; body: string }> = []
  for (const file of candidates) {
    const body = await fs.readFile(file, "utf8").catch(() => "")
    if (body.trim()) out.push({ file: path.relative(caseDir, file), body })
  }
  return out
}

const countNeedle = (text: string, needle: string): number => text.split(needle).length - 1

const maxBlankRun = (lines: readonly string[]): number => {
  let max = 0
  let current = 0
  for (const line of lines) {
    if (line.trim().length === 0) {
      current += 1
      max = Math.max(max, current)
    } else {
      current = 0
    }
  }
  return max
}

const trimEmptyEdges = (lines: readonly string[]): readonly string[] => {
  let start = 0
  let end = lines.length
  while (start < end && lines[start]?.trim().length === 0) start += 1
  while (end > start && lines[end - 1]?.trim().length === 0) end -= 1
  return lines.slice(start, end)
}

const hasModelPicker = (body: string): boolean =>
  body.includes("Select model") || body.includes("Select a model") || body.includes("Models") || body.includes("Loading model catalog")

const hasClosedComposerProbe = (body: string): boolean =>
  body.includes("picker-close-probe") || body.includes("enter send") || body.includes("Type your request")

const hasPriorContext = (lines: readonly string[], overlayLineIndex: number): boolean => {
  const prior = lines.slice(0, Math.max(0, overlayLineIndex)).join("\n")
  return prior.includes("BreadBoard") || prior.includes("Hello again") || prior.includes("❯") || prior.includes("PRE_APP_ALPHA")
}

export const evaluateOverlayFootprint = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await readTextFiles(caseDir)
  for (const { file, body } of files) {
    const sections = body.split(/\n(?=# )/)
    for (const [sectionIndex, sectionBody] of sections.entries()) {
    if (!hasModelPicker(sectionBody)) continue
    const scopedFile = sections.length > 1 ? `${file}#section-${sectionIndex + 1}` : file
    const rawLines = sectionBody.split(/\r?\n/)
    const labelLine = rawLines[0] ?? ""
    const lines = labelLine.startsWith("# ") ? rawLines.slice(1) : rawLines
    const contentBody = lines.join("\n")
    const modelTitleCount = countNeedle(contentBody, "Select model") + countNeedle(contentBody, "Select a model")
    if (modelTitleCount > 1) {
      anomalies.push({
        id: "model-picker-duplicated",
        message: `${scopedFile} contains ${modelTitleCount} model-picker title occurrences.`,
      })
    }
    const overlayIndex = lines.findIndex((line) =>
      line.includes("Select model") || line.includes("Select a model") || line.includes("Loading model catalog"),
    )
    if (overlayIndex >= 0 && !hasPriorContext(lines, overlayIndex)) {
      anomalies.push({
        id: "model-picker-context-missing",
        message: `${scopedFile} shows the model picker without prior terminal/context rows.`,
      })
    }
    const gapBeforeOverlay = overlayIndex > 0 ? maxBlankRun(lines.slice(0, overlayIndex + 1)) : 0
    if (gapBeforeOverlay > 4) {
      anomalies.push({
        id: "model-picker-large-pre-overlay-gap",
        message: `${scopedFile} has a ${gapBeforeOverlay}-line blank gap before the model picker.`,
      })
    }
    const interiorSnapshotGap = maxBlankRun(trimEmptyEdges(lines))
    if (interiorSnapshotGap > 6) {
      anomalies.push({
        id: "overlay-large-blank-gap",
        message: `${scopedFile} has a ${interiorSnapshotGap}-line interior blank run while an overlay is visible.`,
      })
    }
    if (labelLine.includes("model-picker-closed") && hasClosedComposerProbe(contentBody)) {
      const stalePickerAfterClose =
        contentBody.includes("Loading model catalog") ||
        contentBody.includes("│  Models") ||
        contentBody.includes("Select a model")
      if (stalePickerAfterClose) {
        anomalies.push({
          id: "model-picker-stale-after-close",
          message: `${scopedFile} retains model-picker rows after the picker was closed.`,
        })
      }
    }
    }
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateOverlayFootprint(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error))
    process.exit(1)
  })
}
