import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { promises as fs } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const TUI_ROOT = path.resolve(__dirname, "..")
const REPO_ROOT = path.resolve(TUI_ROOT, "..")
const PHASE_DIR = path.join(
  REPO_ROOT,
  "docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete",
)
const OUT_ROOT = path.join(PHASE_DIR, "artifacts/goal_recovery")

type Scenario = {
  id: string
  title: string
  dirName: string
  requiredMarkers: readonly string[]
  forbiddenMarkers: readonly string[]
}

const SCENARIOS: readonly Scenario[] = [
  {
    id: "goal_fork",
    title: "/goal and /fork feature-gated exact invocation",
    dirName: "goal_fork",
    requiredMarkers: ["Status: feature-gated", "durable goal", "session graph", "Doctor", "recommendation="],
    forbiddenMarkers: ["TASK COMPLETE", "Tool execution results", "You are Codex"],
  },
  {
    id: "doctor_footer",
    title: "/doctor lifecycle diagnostics",
    dirName: "doctor_footer",
    requiredMarkers: ["Doctor", "engineMode=local-owned", "restartAvailable=yes", "recommendation="],
    forbiddenMarkers: ["Disconnected:", "Lost connection to the engine"],
  },
  {
    id: "retry_after_unknown",
    title: "/retry after unknown submitted prompt",
    dirName: "retry_after_unknown",
    requiredMarkers: ["V7_RETRY_RECOVERED_RESPONSE", "Submitted prompt outcome is unknown", "Resubmitting prompt"],
    forbiddenMarkers: ["Disconnected:", "Lost connection to the engine"],
  },
  {
    id: "resume_after_recovery",
    title: "/resume after owned-engine recovery",
    dirName: "resume_after_recovery",
    requiredMarkers: ["resume=recovered-new-session", "localTranscript=preserved", "engine context was reset"],
    forbiddenMarkers: ["Disconnected:", "Lost connection to the engine"],
  },
  {
    id: "stop_during_recovery",
    title: "/stop during recovery",
    dirName: "stop_during_recovery",
    requiredMarkers: ["recovery=stopped", "/retry to resubmit", "Recovery stopped"],
    forbiddenMarkers: ["TASK COMPLETE", "Tool execution results"],
  },
  {
    id: "resize_during_reconnect",
    title: "Resize while recovery/reconnect state is visible",
    dirName: "resize_during_reconnect",
    requiredMarkers: ["Recovery needed", "Submitted prompt outcome is unknown", "V7_TOOL_STDOUT_LINE_A"],
    forbiddenMarkers: ["Disconnected:", "Lost connection to the engine"],
  },
]

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const run = (command: string, args: readonly string[], cwd = REPO_ROOT): Promise<{ code: number; stdout: string; stderr: string }> =>
  new Promise((resolve) => {
    const child = spawn(command, [...args], { cwd, stdio: ["ignore", "pipe", "pipe"] })
    const stdout: Buffer[] = []
    const stderr: Buffer[] = []
    child.stdout?.on("data", (chunk) => stdout.push(Buffer.from(chunk)))
    child.stderr?.on("data", (chunk) => stderr.push(Buffer.from(chunk)))
    child.on("error", (error) => resolve({ code: 127, stdout: "", stderr: String(error) }))
    child.on("exit", (code) =>
      resolve({
        code: code ?? 1,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      }),
    )
  })

const escapeXml = (value: string): string =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")

const parseSnapshots = (value: string): Array<{ label: string; body: string }> => {
  const parts = value.split(/^# /m)
  const out: Array<{ label: string; body: string }> = []
  for (const part of parts) {
    const trimmed = part.trimEnd()
    if (!trimmed) continue
    const newline = trimmed.indexOf("\n")
    if (newline === -1) continue
    out.push({ label: trimmed.slice(0, newline).trim(), body: trimmed.slice(newline + 1) })
  }
  return out
}

const chooseSnapshot = (snapshotsText: string, markers: readonly string[]): { label: string | null; text: string } => {
  const snapshots = parseSnapshots(snapshotsText)
  let best = { label: null as string | null, text: snapshotsText, score: -1 }
  for (const snapshot of snapshots) {
    const score = markers.filter((marker) => snapshot.body.includes(marker)).length
    if (score > best.score) best = { label: snapshot.label, text: snapshot.body, score }
  }
  return best
}

const clipLines = (value: string, maxLines: number, maxChars: number): string[] => {
  const lines = value.replace(/\r/g, "").split("\n").filter((line) => line.trim().length > 0)
  return lines.slice(0, maxLines).map((line) => (line.length > maxChars ? `${line.slice(0, maxChars - 1)}…` : line))
}

const scenarioDir = (artifactDir: string, scenario: Scenario): string => path.join(artifactDir, scenario.dirName)

const summarize = async (artifactDir: string, scenario: Scenario) => {
  const dir = scenarioDir(artifactDir, scenario)
  const snapshots = await readText(path.join(dir, "snapshots.txt"))
  const report = await readText(path.join(dir, "gate_report.md"))
  const failures = await readText(path.join(dir, "gate_failures.txt"))
  const harness = await readText(path.join(dir, "harness_output.txt"))
  const combined = `${snapshots}\n${report}\n${harness}`
  const missingMarkers = scenario.requiredMarkers.filter((marker) => !combined.includes(marker))
  const forbiddenHits = scenario.forbiddenMarkers.filter((marker) => combined.includes(marker))
  const gateFailed = failures.trim().length > 0 || /failures:\s*[1-9]/i.test(report)
  const snapshot = chooseSnapshot(snapshots, scenario.requiredMarkers)
  return {
    id: scenario.id,
    title: scenario.title,
    dir,
    gateReport: existsSync(path.join(dir, "gate_report.md")) ? path.join(dir, "gate_report.md") : null,
    snapshots: existsSync(path.join(dir, "snapshots.txt")) ? path.join(dir, "snapshots.txt") : null,
    missingMarkers,
    forbiddenHits,
    gateFailed,
    pass: missingMarkers.length === 0 && forbiddenHits.length === 0 && !gateFailed,
    visualSnapshotLabel: snapshot.label,
    visualSnapshotText: snapshot.text,
  }
}

const buildSvg = (summaries: readonly Awaited<ReturnType<typeof summarize>>[]): string => {
  const panelWidth = 900
  const panelHeight = 285
  const gap = 18
  const width = panelWidth + 48
  const height = 78 + summaries.length * (panelHeight + gap) + 24
  const out: string[] = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<rect width="100%" height="100%" fill="#0b0a08"/>`,
    `<text x="24" y="32" fill="#f8b36a" font-size="22" font-family="DejaVu Sans" font-weight="800">P16 Phase H Goal / Fork / Recovery Evidence</text>`,
    `<text x="24" y="56" fill="#a8a29e" font-size="13" font-family="DejaVu Sans">Feature-gated parity commands plus owned-engine recovery/session lifecycle gates.</text>`,
  ]
  let y = 78
  for (const summary of summaries) {
    const ok = summary.pass
    out.push(`<rect x="24" y="${y}" width="${panelWidth}" height="${panelHeight}" rx="12" fill="#15110d" stroke="${ok ? "#5b7c42" : "#cc3f51"}" stroke-width="2"/>`)
    out.push(`<text x="42" y="${y + 26}" fill="${ok ? "#9fc36a" : "#ff6b81"}" font-size="15" font-family="DejaVu Sans Mono" font-weight="700">${escapeXml(summary.id)} · ${ok ? "PASS" : "RED"}</text>`)
    out.push(`<text x="42" y="${y + 47}" fill="#d6d3d1" font-size="12" font-family="DejaVu Sans Mono">missing=${summary.missingMarkers.length} forbidden=${summary.forbiddenHits.length} gateFailed=${summary.gateFailed ? "yes" : "no"} snapshot=${escapeXml(summary.visualSnapshotLabel ?? "none")}</text>`)
    let lineY = y + 70
    for (const line of clipLines(summary.visualSnapshotText, 16, 130)) {
      out.push(`<text x="42" y="${lineY}" fill="#e7e5e4" font-size="11" font-family="DejaVu Sans Mono">${escapeXml(line)}</text>`)
      lineY += 13
      if (lineY > y + panelHeight - 12) break
    }
    y += panelHeight + gap
  }
  out.push("</svg>")
  return out.join("\n")
}

const resolveArtifactDir = async (): Promise<string> => {
  if (process.env.P16_PHASE_H_ARTIFACT_DIR) return path.resolve(process.env.P16_PHASE_H_ARTIFACT_DIR)
  const entries = await fs.readdir(OUT_ROOT, { withFileTypes: true }).catch(() => [])
  const candidates = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("phase_h_goal_recovery_"))
      .map(async (entry) => {
        const dir = path.join(OUT_ROOT, entry.name)
        const stat = await fs.stat(dir)
        return { dir, mtime: stat.mtimeMs }
      }),
  )
  candidates.sort((a, b) => b.mtime - a.mtime)
  const latest = candidates[0]?.dir
  if (!latest) throw new Error(`No Phase H artifact directory found under ${OUT_ROOT}`)
  return latest
}

const main = async (): Promise<void> => {
  const artifactDir = await resolveArtifactDir()
  await fs.mkdir(OUT_ROOT, { recursive: true })
  const summaries = await Promise.all(SCENARIOS.map((scenario) => summarize(artifactDir, scenario)))
  const failures = summaries.flatMap((summary) => {
    const out: string[] = []
    if (summary.gateFailed) out.push(`${summary.id}: gate failed`)
    for (const marker of summary.missingMarkers) out.push(`${summary.id}: missing marker ${marker}`)
    for (const marker of summary.forbiddenHits) out.push(`${summary.id}: forbidden marker ${marker}`)
    return out
  })
  const svg = buildSvg(summaries)
  const svgPath = path.join(OUT_ROOT, "goal_recovery_contact_sheet.svg")
  const pngPath = path.join(OUT_ROOT, "goal_recovery_contact_sheet.png")
  const mdPath = path.join(OUT_ROOT, "goal_recovery_contact_sheet.md")
  await fs.writeFile(svgPath, `${svg}\n`, "utf8")
  const convert = await run("convert", [svgPath, pngPath], REPO_ROOT)
  if (convert.code !== 0) failures.push(`ImageMagick convert failed: ${convert.stderr || convert.stdout}`)
  await fs.writeFile(
    mdPath,
    [
      "# P16 Phase H Goal / Fork / Recovery Contact Sheet",
      "",
      `Source artifact dir: \`${artifactDir}\``,
      "",
      ...summaries.map((summary) => `- ${summary.pass ? "PASS" : "RED"} ${summary.id}: ${summary.title}`),
      "",
    ].join("\n"),
    "utf8",
  )
  const report = {
    artifactDir,
    generatedAt: new Date().toISOString(),
    summaries,
    artifacts: {
      svgPath,
      pngPath: convert.code === 0 ? pngPath : null,
      mdPath,
    },
    failures,
  }
  await fs.writeFile(path.join(OUT_ROOT, "phase_h_goal_recovery_evidence_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  if (failures.length > 0) {
    console.error(failures.map((item) => `[p16][phase-h] ${item}`).join("\n"))
    process.exit(1)
  }
  console.log(JSON.stringify(report, null, 2))
}

main().catch((error) => {
  console.error(`[p16][phase-h] ${(error as Error).message}`)
  process.exit(1)
})
