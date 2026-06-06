import { spawn } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const REPO_DIR = path.resolve(__dirname, "..")
const ROOT_DIR = path.resolve(REPO_DIR, "..")
const PHASE_DIR = path.join(
  ROOT_DIR,
  "docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete",
)
const OUT_DIR = path.join(PHASE_DIR, "artifacts/multiagent")

const REQUIRED_CASES = [
  "agents_taskboard_entrypoint",
  "multiagent_tasks_panel",
  "multiagent_task_focus_controls",
  "multiagent_task_slash_inspect_follow",
  "multiagent_task_log_export",
  "multiagent_failure_retry",
  "multiagent_task_action_guards",
  "multiagent_task_action_command",
] as const

const CASE_MARKERS: Record<string, readonly string[]> = {
  agents_taskboard_entrypoint: [
    "Background tasks",
    "Task controls live in Task Focus",
    "ID: task-implementer-01",
  ],
  multiagent_tasks_panel: [
    "Background tasks",
    "Implement SMTP server core",
    "Run SMTP smoke tests",
    "Reviewer finished requirements pass",
  ],
  multiagent_task_focus_controls: [
    "Task Focus",
    "Task controls: X cancel",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ],
  multiagent_task_slash_inspect_follow: [
    "Task Focus",
    "Task Focus follow paused.",
    "Task Focus follow: resumed.",
    "Scope: local Task Focus tail only; no task state mutation or model submission occurred.",
  ],
  multiagent_task_log_export: [
    "Task log saved to",
    "SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker",
  ],
  multiagent_failure_retry: [
    "Failure summary: tester failed retryable",
    "SMTP_TEST_FAILURE missing DATA terminator",
    "FAILURE_TAIL_LINE_05 deterministic failure marker",
  ],
  multiagent_task_action_guards: [
    "Mutation: unavailable until engine task-control endpoints exist.",
    "cancel unavailable for task-implementer-01.",
    "Reason: engine task mutation endpoint is not exposed yet.",
  ],
  multiagent_task_action_command: [
    "Mutation: engine-backed task controls enabled.",
    "cancel requested for task-implementer-01.",
    "cancelled",
  ],
}

type CaseSummary = {
  id: string
  caseDir: string
  anomalies: number
  contractErrors: number
  contractWarnings: number
  markersMissing: string[]
  visualSnapshotLabel: string | null
  visualSnapshotText: string
}

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const readJson = async <T>(filePath: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8")) as T
  } catch {
    return null
  }
}

const run = async (cmd: string, args: string[], cwd: string): Promise<boolean> =>
  await new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: "ignore" })
    child.on("error", () => resolve(false))
    child.on("close", (code) => resolve(code === 0))
  })

const findLatestBatchDir = async (): Promise<string> => {
  if (process.env.P16_PHASE_G_BATCH_DIR) return path.resolve(process.env.P16_PHASE_G_BATCH_DIR)
  const candidates: Array<{ file: string; mtime: number }> = []
  const scan = async (dir: string): Promise<void> => {
    let entries: Awaited<ReturnType<typeof fs.readdir>>
    try {
      entries = await fs.readdir(dir, { withFileTypes: true })
    } catch {
      return
    }
    for (const entry of entries) {
      const resolved = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        await scan(resolved)
      } else if (entry.isFile() && entry.name === "manifest.json") {
        const stat = await fs.stat(resolved)
        candidates.push({ file: resolved, mtime: stat.mtimeMs })
      }
    }
  }
  await scan(OUT_DIR)
  candidates.sort((left, right) => right.mtime - left.mtime)
  const latest = candidates[0]?.file
  if (!latest) throw new Error(`No Phase G multiagent manifest found under ${OUT_DIR}`)
  return path.dirname(latest)
}

const escapeXml = (value: string): string =>
  value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")

const clipLines = (value: string, maxLines: number, maxChars: number): string[] => {
  const lines = value.replace(/\r/g, "").split("\n").filter((line) => line.trim().length > 0)
  const tail = lines.slice(Math.max(0, lines.length - maxLines))
  return tail.map((line) => (line.length > maxChars ? `${line.slice(0, maxChars - 1)}…` : line))
}

const parseSnapshots = (value: string): Array<{ label: string; body: string }> => {
  const parts = value.split(/^# /m)
  const snapshots: Array<{ label: string; body: string }> = []
  for (const part of parts) {
    const trimmed = part.trimEnd()
    if (!trimmed) continue
    const newline = trimmed.indexOf("\n")
    if (newline === -1) continue
    snapshots.push({ label: trimmed.slice(0, newline).trim(), body: trimmed.slice(newline + 1) })
  }
  return snapshots
}

const chooseVisualSnapshot = (
  snapshotsText: string,
  fallbackText: string,
  markers: readonly string[],
): { label: string | null; text: string } => {
  const snapshots = parseSnapshots(snapshotsText)
  let best: { label: string | null; text: string; score: number } = { label: null, text: fallbackText, score: -1 }
  for (const snapshot of snapshots) {
    const score = markers.filter((marker) => snapshot.body.includes(marker)).length
    const hasOverlay =
      snapshot.body.includes("Background tasks") ||
      snapshot.body.includes("Task Focus") ||
      snapshot.body.includes("Task log saved to") ||
      snapshot.body.includes("Task action applied")
    const adjusted = score * 10 + (hasOverlay ? 5 : 0)
    if (adjusted > best.score) best = { label: snapshot.label, text: snapshot.body, score: adjusted }
  }
  return { label: best.label, text: best.text }
}

const buildContactSheetSvg = async (summaries: readonly CaseSummary[]): Promise<string> => {
  const panelWidth = 880
  const panelHeight = 300
  const gap = 18
  const titleHeight = 72
  const width = panelWidth + 48
  const height = titleHeight + summaries.length * (panelHeight + gap) + 24
  const out: string[] = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    `<rect width="100%" height="100%" fill="#0b0a08"/>`,
    `<text x="24" y="32" fill="#f8b36a" font-size="22" font-family="DejaVu Sans" font-weight="800">P16 Phase G Multiagent Evidence</text>`,
    `<text x="24" y="55" fill="#a8a29e" font-size="13" font-family="DejaVu Sans">Production PTY snapshots; accepted only where anomalies, contract errors, and required markers are clean.</text>`,
  ]
  let y = titleHeight
  for (const summary of summaries) {
    const lines = clipLines(summary.visualSnapshotText, 18, 128)
    const ok = summary.anomalies === 0 && summary.contractErrors === 0 && summary.markersMissing.length === 0
    out.push(`<rect x="24" y="${y}" width="${panelWidth}" height="${panelHeight}" rx="12" fill="#15110d" stroke="${ok ? "#5b7c42" : "#cc3f51"}" stroke-width="2"/>`)
    out.push(`<text x="42" y="${y + 26}" fill="${ok ? "#9fc36a" : "#ff6b81"}" font-size="15" font-family="DejaVu Sans Mono" font-weight="700">${escapeXml(summary.id)} · ${ok ? "PASS" : "RED"}</text>`)
    out.push(`<text x="42" y="${y + 47}" fill="#d6d3d1" font-size="12" font-family="DejaVu Sans Mono">anomalies=${summary.anomalies} contractErrors=${summary.contractErrors} missingMarkers=${summary.markersMissing.length}</text>`)
    if (summary.visualSnapshotLabel) {
      out.push(`<text x="500" y="${y + 47}" fill="#a8a29e" font-size="12" font-family="DejaVu Sans Mono">snapshot=${escapeXml(summary.visualSnapshotLabel)}</text>`)
    }
    let lineY = y + 70
    for (const line of lines) {
      out.push(`<text x="42" y="${lineY}" fill="#e7e5e4" font-size="11" font-family="DejaVu Sans Mono">${escapeXml(line)}</text>`)
      lineY += 13
      if (lineY > y + panelHeight - 14) break
    }
    y += panelHeight + gap
  }
  out.push("</svg>")
  return out.join("\n")
}

const summarizeCase = async (batchDir: string, id: string): Promise<CaseSummary> => {
  const caseDir = path.join(batchDir, id)
  const anomalies = await readJson<unknown[]>(path.join(caseDir, "anomalies.json"))
  const contract = await readJson<{ errors?: unknown[]; warnings?: unknown[] }>(path.join(caseDir, "contract_report.json"))
  const snapshotsText = await readText(path.join(caseDir, "pty_snapshots.txt"))
  const plainText = await readText(path.join(caseDir, "pty_plain.txt"))
  const text = `${snapshotsText}\n${plainText}`
  const markersMissing = CASE_MARKERS[id].filter((marker) => !text.includes(marker))
  const visual = chooseVisualSnapshot(snapshotsText, await readText(path.join(caseDir, "grid_snapshots/final.txt")) || plainText, CASE_MARKERS[id])
  return {
    id,
    caseDir,
    anomalies: Array.isArray(anomalies) ? anomalies.length : 999,
    contractErrors: Array.isArray(contract?.errors) ? contract.errors.length : 999,
    contractWarnings: Array.isArray(contract?.warnings) ? contract.warnings.length : 0,
    markersMissing,
    visualSnapshotLabel: visual.label,
    visualSnapshotText: visual.text,
  }
}

const copyLatestTaskLogSample = async (targetDir: string): Promise<string | null> => {
  const sourceDir = path.join(REPO_DIR, "artifacts/task-logs")
  let entries: Array<{ file: string; mtime: number }> = []
  try {
    entries = await Promise.all(
      (await fs.readdir(sourceDir, { withFileTypes: true }))
        .filter((entry) => entry.isFile() && /^task-log-task-implementer-01-.*\.jsonl$/.test(entry.name))
        .map(async (entry) => {
          const file = path.join(sourceDir, entry.name)
          const stat = await fs.stat(file)
          return { file, mtime: stat.mtimeMs }
        }),
    )
  } catch {
    return null
  }
  entries.sort((left, right) => right.mtime - left.mtime)
  const latest = entries[0]?.file
  if (!latest) return null
  await fs.mkdir(targetDir, { recursive: true })
  const target = path.join(targetDir, path.basename(latest))
  await fs.copyFile(latest, target)
  return target
}

const main = async (): Promise<void> => {
  await fs.mkdir(OUT_DIR, { recursive: true })
  const batchDir = await findLatestBatchDir()
  const summaries = await Promise.all(REQUIRED_CASES.map((id) => summarizeCase(batchDir, id)))
  const failures = summaries.flatMap((summary) => {
    const issues: string[] = []
    if (summary.anomalies !== 0) issues.push(`${summary.id}: anomalies=${summary.anomalies}`)
    if (summary.contractErrors !== 0) issues.push(`${summary.id}: contractErrors=${summary.contractErrors}`)
    for (const marker of summary.markersMissing) issues.push(`${summary.id}: missing marker ${marker}`)
    return issues
  })
  const sample = await copyLatestTaskLogSample(path.join(OUT_DIR, "task_log_export_samples"))
  if (!sample) failures.push("task_log_export_samples: no task-log-task-implementer-01 JSONL sample copied")

  const svg = await buildContactSheetSvg(summaries)
  const svgPath = path.join(OUT_DIR, "multiagent_contact_sheet.svg")
  const pngPath = path.join(OUT_DIR, "multiagent_contact_sheet.png")
  const mdPath = path.join(OUT_DIR, "multiagent_contact_sheet.md")
  await fs.writeFile(svgPath, `${svg}\n`, "utf8")
  const pngCreated = await run("convert", [svgPath, pngPath], ROOT_DIR)
  if (!pngCreated) failures.push("multiagent_contact_sheet.png: ImageMagick convert failed")

  const report = {
    generatedAt: new Date().toISOString(),
    batchDir,
    verdict: failures.length === 0 ? "pass" : "fail",
    failures,
    cases: summaries,
    taskLogSample: sample,
    contactSheet: {
      svgPath,
      pngPath: pngCreated ? pngPath : null,
      mdPath,
    },
    nonClaims: [
      "Full TUI agent creation/invocation UX is not proven by this artifact.",
      "Live engine pause/resume/retry/merge semantics are not proven by this artifact.",
      "Session resume/persistence and host-lane multiagent universality are not proven by this artifact.",
    ],
  }
  await fs.writeFile(path.join(OUT_DIR, "phase_g_multiagent_evidence_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  await fs.writeFile(
    mdPath,
    [
      "# P16 Phase G Multiagent Contact Sheet",
      "",
      `- verdict: \`${report.verdict}\``,
      `- batch: \`${path.relative(ROOT_DIR, batchDir)}\``,
      `- task_log_sample: \`${sample ? path.relative(ROOT_DIR, sample) : "missing"}\``,
      "",
      "| Case | Anomalies | Contract errors | Missing markers |",
      "| --- | ---: | ---: | --- |",
      ...summaries.map((summary) => `| ${summary.id} | ${summary.anomalies} | ${summary.contractErrors} | ${summary.markersMissing.join("; ") || "none"} |`),
      "",
    ].join("\n"),
    "utf8",
  )

  console.log(JSON.stringify(report, null, 2))
  if (failures.length > 0) process.exit(1)
}

void main().catch((error) => {
  console.error(`[p16-phase-g] ${(error as Error).message}`)
  process.exit(1)
})
