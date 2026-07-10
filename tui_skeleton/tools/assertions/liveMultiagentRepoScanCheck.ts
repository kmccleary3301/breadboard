import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir = ""
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i] ?? ""
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

const parseSnapshots = (text: string): Map<string, string> => {
  const snapshots = new Map<string, string>()
  const parts = text.split(/^# /m)
  for (const part of parts) {
    const trimmed = part.trimEnd()
    if (!trimmed) continue
    const newline = trimmed.indexOf("\n")
    if (newline === -1) continue
    snapshots.set(trimmed.slice(0, newline).trim(), trimmed.slice(newline + 1))
  }
  return snapshots
}

const idPart = (value: string): string => value.replace(/[^a-z0-9]+/gi, "-").toLowerCase()

const assistantMessagesFromSse = (text: string): string[] => {
  const messages: string[] = []
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith("data: ")) continue
    try {
      const event = JSON.parse(line.slice("data: ".length))
      if (event?.type !== "assistant_message") continue
      const payloadText = event?.payload?.text
      if (typeof payloadText === "string" && payloadText.trim()) messages.push(payloadText)
    } catch {
      // Ignore non-JSON SSE lines; the assertion only needs canonical events.
    }
  }
  return messages
}

const requireSnapshot = (
  snapshots: Map<string, string>,
  label: string,
  required: readonly string[],
  anomalies: LayoutAnomaly[],
): string => {
  const body = snapshots.get(label) ?? ""
  if (!body.trim()) {
    anomalies.push({ id: `missing-${label}`, message: `Snapshot ${label} is missing or empty.` })
    return body
  }
  for (const needle of required) {
    if (!body.includes(needle)) {
      anomalies.push({ id: `missing-${label}-${idPart(needle)}`, message: `Snapshot ${label} is missing expected text: ${needle}` })
    }
  }
  return body
}

export const evaluateLiveMultiagentRepoScan = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const snapshotsText = await readText(path.join(caseDir, "pty_snapshots.txt"))
  const plain = await readText(path.join(caseDir, "pty_plain.txt"))
  const events = await readText(path.join(caseDir, "events.ndjson"))
  const sse = await readText(path.join(caseDir, "sse_events.txt"))
  const state = await readText(path.join(caseDir, "repl_state.ndjson"))
  const caseInfo = await readText(path.join(caseDir, "case_info.json"))
  const snapshots = parseSnapshots(snapshotsText)

  requireSnapshot(snapshots, "live-multiagent-final-history", [
    "LIVE_MULTIAGENT_REPO_SCAN_DONE",
    "README.md",
    "package.json",
  ], anomalies)

  requireSnapshot(snapshots, "live-multiagent-agents-taskboard", [
    "Background tasks",
    "repo-scanner",
  ], anomalies)

  requireSnapshot(snapshots, "live-multiagent-agents-taskboard-resized", [
    "Background tasks",
    "repo-scanner",
  ], anomalies)

  const closed = requireSnapshot(snapshots, "live-multiagent-closed", ["enter send"], anomalies)
  if (closed.includes("Background tasks")) {
    anomalies.push({ id: "live-multiagent-taskboard-still-open", message: "Taskboard remained visible after Escape." })
  }

  if (!events.includes('"type":"task_event"') && !state.includes('"liveSlots"')) {
    anomalies.push({ id: "missing-live-task-event", message: "Live multiagent run did not expose task events to the TUI artifacts." })
  }
  if (!plain.includes("❯ This is a live multiagent verification run")) {
    anomalies.push({ id: "missing-live-user-prompt", message: "Submitted live multiagent prompt was not preserved in the readable transcript." })
  }
  const finalAssistant = assistantMessagesFromSse(sse).at(-1) ?? ""
  for (const needle of ["LIVE_MULTIAGENT_REPO_SCAN_DONE", "README.md", "package.json"]) {
    if (!finalAssistant.includes(needle)) {
      anomalies.push({ id: `missing-final-assistant-${idPart(needle)}`, message: `Final assistant answer is missing expected task-result text: ${needle}` })
    }
  }
  if (!events.includes('"tool":"TaskOutput"') && !events.includes('"tool":"taskoutput"')) {
    anomalies.push({ id: "missing-taskoutput-retrieval", message: "Live multiagent run launched an async task but did not retrieve the result with TaskOutput." })
  }
  if (!caseInfo.includes("codex_cli_gpt54mini_e4_live_multiagent.yaml")) {
    anomalies.push({ id: "wrong-live-multiagent-config", message: "Case did not run with the Codex GPT-5.4-mini multiagent live config." })
  }
  for (const forbidden of ["Unknown tool task", "unknown tool task", "Unknown agent type", "system prompt", "don’t have the repo-scanner", "don't have the repo-scanner", "completed output yet"]) {
    if (plain.includes(forbidden) || snapshotsText.includes(forbidden)) {
      anomalies.push({ id: `forbidden-${idPart(forbidden)}`, message: `Live multiagent artifacts include forbidden text: ${forbidden}` })
    }
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveMultiagentRepoScan(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
