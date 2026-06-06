import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

interface SnapshotSection {
  readonly label: string
  readonly body: string
}

interface StateDumpRecord {
  readonly reason?: string
  readonly state?: Record<string, any>
}

const parseSections = (text: string): SnapshotSection[] => {
  const lines = text.split(/\r?\n/)
  const sections: SnapshotSection[] = []
  let currentLabel = "snapshot"
  let currentBody: string[] = []
  const flush = () => {
    sections.push({ label: currentLabel, body: currentBody.join("\n") })
    currentBody = []
  }
  for (const line of lines) {
    if (line.startsWith("# ")) {
      if (currentBody.length > 0 || sections.length > 0) flush()
      currentLabel = line.slice(2).trim() || "snapshot"
      continue
    }
    currentBody.push(line)
  }
  flush()
  return sections.filter((section) => section.body.trim().length > 0)
}

const countMatches = (text: string, pattern: RegExp): number => text.match(pattern)?.length ?? 0

const readStateDump = async (target: string): Promise<StateDumpRecord[]> => {
  const raw = await fs.readFile(target, "utf8")
  return raw
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line, index) => {
      try {
        return JSON.parse(line) as StateDumpRecord
      } catch (error) {
        throw new Error(`Invalid state dump JSON on line ${index + 1}: ${(error as Error).message}`)
      }
    })
}

const resolvePath = (target: string): string => path.isAbsolute(target) ? target : path.join(process.cwd(), target)

const main = async () => {
  const snapshotArg = process.argv[2]?.trim()
  const stateArg = process.argv[3]?.trim()
  if (!snapshotArg || !stateArg) {
    throw new Error("Usage: qc_disconnect_render_integrity_gate.ts <snapshots.txt> <state.ndjson>")
  }
  const snapshotPath = resolvePath(snapshotArg)
  const statePath = resolvePath(stateArg)
  const sections = parseSections(await fs.readFile(snapshotPath, "utf8"))
  const records = await readStateDump(statePath)
  const failures: string[] = []
  if (sections.length === 0) failures.push("no snapshot sections captured")
  if (records.length === 0) failures.push("no state dump records captured")

  for (const section of sections) {
    const bodyNetworkBannerCount = countMatches(section.body, /\b(?:Reconnecting|Disconnected):\s/g)
    const promptCount = countMatches(section.body, /❯ Show me some markdown\./g)
    const toolCount = countMatches(section.body, /● Tool/g)
    const staleBoxFragmentCount = countMatches(section.body, /(?:Reconnecting|Disconnected):[\s\S]{0,200}\n\s*╭/g)
    if (bodyNetworkBannerCount > 0) {
      failures.push(`[${section.label}] transient network banner rendered in preserved scrollback body (${bodyNetworkBannerCount})`)
    }
    if (promptCount > 1) {
      failures.push(`[${section.label}] duplicate submitted prompt rendered in one snapshot (${promptCount})`)
    }
    if (section.label.includes("disconnect") && toolCount < 1) {
      failures.push(`[${section.label}] expected completed tool output to remain visible after disconnect`)
    }
    if (staleBoxFragmentCount > 0) {
      failures.push(`[${section.label}] stale/partial network box fragment detected (${staleBoxFragmentCount})`)
    }
  }

  for (let index = 0; index < records.length; index += 1) {
    const state = records[index]?.state ?? {}
    const status = String(state.status ?? "")
    const pending = state.pendingResponse === true
    const disconnected = state.disconnected === true
    if (status.startsWith("Reconnecting") && !pending) {
      failures.push(`[state ${index}] reconnecting state is not pending: ${status}`)
    }
    if (disconnected && status === "Ready") {
      failures.push(`[state ${index}] disconnected state was overwritten with Ready`)
    }
  }

  const finalState = records.at(-1)?.state ?? {}
  const finalConversation = Array.isArray(finalState.conversation) ? finalState.conversation : []
  const finalTranscriptCells = Array.isArray(finalState.transcriptCells) ? finalState.transcriptCells : []
  const finalUserPromptsFromConversation = finalConversation.filter(
    (entry) => entry?.speaker === "user" && String(entry?.text ?? "").trim() === "Show me some markdown.",
  )
  const finalUserPromptsFromCells = finalTranscriptCells.filter(
    (entry) => entry?.role === "user-request" && String(entry?.textPreview ?? "").trim() === "Show me some markdown.",
  )
  const finalUserPromptCount =
    finalUserPromptsFromConversation.length > 0
      ? finalUserPromptsFromConversation.length
      : finalUserPromptsFromCells.length
  const finalToolEvents = Array.isArray(finalState.toolEvents) ? finalState.toolEvents : []
  const finalToolCells = finalTranscriptCells.filter((entry) => entry?.kind === "tool")
  const finalToolCount = finalToolEvents.length > 0 ? finalToolEvents.length : finalToolCells.length
  if (finalUserPromptCount !== 1) {
    failures.push(`final state should contain exactly one submitted user prompt; found ${finalUserPromptCount}`)
  }
  if (finalToolCount < 1) {
    failures.push("final state should preserve at least one completed tool event")
  }
  if (finalState.disconnected !== true || finalState.status !== "Disconnected") {
    failures.push(`final state should remain Disconnected; got status=${String(finalState.status)} disconnected=${String(finalState.disconnected)}`)
  }

  if (failures.length > 0) {
    throw new Error(`Disconnect render integrity gate failed:\n${failures.join("\n")}`)
  }
  console.log(`[qc] disconnect render integrity gate passed (${sections.length} snapshots, ${records.length} state records)`)
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : String(error)
  console.error(message)
  process.exitCode = 1
})
