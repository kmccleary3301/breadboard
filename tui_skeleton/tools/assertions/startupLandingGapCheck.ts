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

const readStartupFiles = async (caseDir: string): Promise<Array<{ file: string; body: string }>> => {
  const candidates: string[] = []
  const observerDir = path.join(caseDir, "observer_text")
  const observerEntries = await fs.readdir(observerDir).catch(() => [])
  for (const entry of observerEntries) {
    if (entry.toLowerCase().includes("startup") && entry.endsWith(".txt") && !entry.endsWith(".x11.txt") && !entry.endsWith(".summary.txt")) {
      candidates.push(path.join(observerDir, entry))
    }
  }
  const ptySnapshots = path.join(caseDir, "pty_snapshots.txt")
  const terminalVisible = path.join(caseDir, "terminal_text", "visible_final.txt")
  candidates.push(ptySnapshots, terminalVisible)

  const out: Array<{ file: string; body: string }> = []
  for (const file of candidates) {
    try {
      const body = await fs.readFile(file, "utf8")
      if (body.trim()) out.push({ file, body })
    } catch {
      // Not every harness emits every startup surface.
    }
  }
  return out
}

const hasOrientation = (body: string): boolean =>
  /BreadBoard(?:\s+v|\s*·)/.test(body) ||
  body.includes("Tips for getting started")

const hasRichLandingPane = (body: string): boolean =>
  /BreadBoard\s+v/.test(body) ||
  body.includes("Tips for getting started") ||
  body.includes("Using Config")

const allowsCompactOnlyStartup = (relativePath: string): boolean =>
  /smallheight|small-height|constrained-height/.test(relativePath.toLowerCase())

const hasComposer = (body: string): boolean =>
  body.includes("❯") || body.includes("enter send") || body.includes("[ready]") || body.includes("• [ready]")

const countBlankGapBetweenOrientationAndComposer = (body: string): number | null => {
  const lines = body.split(/\r?\n/)
  const orientationEnd = lines.findLastIndex((line) =>
    line.includes("╰") ||
    line.includes("Tips for getting started") ||
    line.includes("BreadBoard ·")
  )
  const composerStart = lines.findIndex((line, index) => index > orientationEnd && (line.includes("❯") || line.includes("• [ready]") || line.includes("[ready]")))
  if (orientationEnd < 0 || composerStart <= orientationEnd) return null
  return lines.slice(orientationEnd + 1, composerStart).filter((line) => line.trim().length === 0).length
}

export const evaluateStartupLandingGap = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const files = await readStartupFiles(caseDir)
  const startupFiles = files.filter(({ file }) => path.basename(file).toLowerCase().includes("startup"))
  const surfaces = startupFiles.length > 0 ? startupFiles : files

  if (surfaces.length === 0) {
    anomalies.push({ id: "startup-capture-missing", message: "No startup capture was available for landing/gap validation." })
    return anomalies
  }

  for (const { file, body } of surfaces) {
    const relative = path.relative(caseDir, file)
    if (!hasOrientation(body)) {
      anomalies.push({
        id: "startup-orientation-missing",
        message: `${relative} does not show a visible landing or compact orientation surface.`,
      })
    }
    if (!hasRichLandingPane(body) && !allowsCompactOnlyStartup(relative)) {
      anomalies.push({
        id: "startup-rich-landing-missing",
        message: `${relative} shows only a compact orientation surface; expected a real landing pane when startup height is not constrained.`,
      })
    }
    if (!hasComposer(body)) {
      anomalies.push({
        id: "startup-composer-missing",
        message: `${relative} does not show a usable composer or ready footer.`,
      })
    }
    const gap = countBlankGapBetweenOrientationAndComposer(body)
    if (gap != null && gap > 3) {
      anomalies.push({
        id: "startup-gap-budget-exceeded",
        message: `${relative} has ${gap} blank lines between orientation and composer; expected <= 3.`,
      })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateStartupLandingGap(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
