import { promises as fs } from "node:fs"
import path from "node:path"

interface Finding {
  readonly id: string
  readonly message: string
}

const read = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir = ""
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i] ?? ""
  }
  if (!caseDir) throw new Error("Usage: tsx scripts/p16_transcript_save_export_gate.ts --case-dir <artifact-dir>")
  return { caseDir: path.resolve(caseDir) }
}

const resolveTuiRoot = (caseDir: string): string => {
  const marker = `${path.sep}docs_tmp${path.sep}`
  const index = caseDir.indexOf(marker)
  if (index > 0) return path.join(caseDir.slice(0, index), "tui_skeleton")
  return process.cwd()
}

const hasLeak = (text: string): string | null => {
  const leakPatterns = [
    /You are Codex/i,
    /Knowledge cutoff/i,
    /developer message/i,
    /system prompt/i,
    /<codex_internal_context/i,
    /# Desired oververbosity/i,
  ]
  const match = leakPatterns.find((pattern) => pattern.test(text))
  return match ? String(match) : null
}

const latestTranscriptCandidate = async (caseDir: string): Promise<{ file: string; contents: string } | null> => {
  const tuiRoot = resolveTuiRoot(caseDir)
  const transcriptDir = path.join(tuiRoot, "artifacts", "transcripts")
  const entries = await fs.readdir(transcriptDir, { withFileTypes: true }).catch(() => [])
  const candidates = [] as Array<{ file: string; mtimeMs: number; contents: string }>
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".txt")) continue
    const file = path.join(transcriptDir, entry.name)
    const stat = await fs.stat(file).catch(() => null)
    if (!stat) continue
    const contents = await read(file)
    candidates.push({ file, mtimeMs: stat.mtimeMs, contents })
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs)
  return candidates[0] ?? null
}

export const evaluateP16TranscriptSaveExport = async (caseDir: string): Promise<Finding[]> => {
  const findings: Finding[] = []
  const snapshots = await read(path.join(caseDir, "pty_snapshots.txt"))
  const raw = await read(path.join(caseDir, "pty_raw.ansi"))
  const state = await read(path.join(caseDir, "repl_state.ndjson"))

  for (const needle of ["# p16-transcript-viewer", "breadboard transcript viewer", "Hello viewer", "Implementation receipts", "# p16-save-feedback", "Saved to", "# p16-after-transcript-return"]) {
    if (!snapshots.includes(needle) && !raw.includes(needle)) {
      findings.push({ id: `missing-${needle.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase()}`, message: `Missing expected transcript/export marker: ${needle}` })
    }
  }

  const latest = await latestTranscriptCandidate(caseDir)
  if (!latest) {
    findings.push({ id: "missing-transcript-file", message: "No transcript export file found." })
  } else {
    for (const needle of ["Hello viewer", "Implementation receipts"]) {
      if (!latest.contents.includes(needle)) {
        findings.push({ id: `export-missing-${needle.toLowerCase().replace(/\s+/g, "-")}`, message: `Latest transcript export ${latest.file} is missing ${needle}.` })
      }
    }
    const leak = hasLeak(latest.contents)
    if (leak) findings.push({ id: "export-leak", message: `Latest transcript export appears to leak internal prompt/config text: ${leak}` })
  }

  if (state.trim()) {
    if (!state.includes("Hello viewer")) findings.push({ id: "state-missing-user", message: "State dump did not include user prompt." })
    if (!state.includes("Implementation receipts")) findings.push({ id: "state-missing-assistant", message: "State dump did not include assistant response marker." })
  }

  const combined = `${snapshots}\n${raw}`
  const leak = hasLeak(combined)
  if (leak) findings.push({ id: "visible-leak", message: `Visible transcript/export surface appears to leak internal prompt/config text: ${leak}` })

  return findings
}

const run = async () => {
  const { caseDir } = parseArgs()
  const findings = await evaluateP16TranscriptSaveExport(caseDir)
  const verdict = findings.length === 0 ? "pass" : "fail"
  process.stdout.write(`${JSON.stringify({ verdict, findings }, null, 2)}\n`)
  if (findings.length > 0) process.exit(1)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
