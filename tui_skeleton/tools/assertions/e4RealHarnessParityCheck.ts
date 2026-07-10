import { promises as fs } from "node:fs"
import os from "node:os"
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

const readOptional = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const parseJsonLines = (raw: string): any[] =>
  raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line)]
      } catch {
        return []
      }
    })

const eventText = (event: any): string => {
  const payload = event?.payload ?? event?.data ?? {}
  const parts = [
    event?.type,
    payload?.text,
    payload?.delta,
    payload?.message?.content,
    payload?.node?.payload?.content,
    payload?.node?.payload?.role,
    payload?.tool?.name,
    payload?.tool?.status,
    payload?.result,
  ]
  return parts.filter((part) => typeof part === "string").join("\n")
}

const countMatchingEvents = (events: any[], predicate: (event: any) => boolean): number =>
  events.reduce((count, event) => count + (predicate(event) ? 1 : 0), 0)

const splitEventsByCompletion = (events: any[]): any[][] => {
  const segments: any[][] = []
  let current: any[] = []
  for (const event of events) {
    current.push(event)
    if (event?.type === "completion" || event?.type === "run_finished") {
      segments.push(current)
      current = []
    }
  }
  if (current.length > 0) segments.push(current)
  return segments
}

const commandFromEvent = (event: any): string => {
  const payload = event?.payload ?? {}
  const candidates = [
    payload?.metadata?.command,
    payload?.call?.function?.arguments,
    payload?.message?.tool_calls?.[0]?.function?.arguments,
    payload?.node?.payload?.tool_calls?.[0]?.function?.arguments,
  ]
  for (const candidate of candidates) {
    if (typeof candidate !== "string" || !candidate.trim()) continue
    try {
      const parsed = JSON.parse(candidate)
      if (typeof parsed?.command === "string") return parsed.command
    } catch {
      return candidate
    }
  }
  return ""
}

const progressUpdatePattern =
  /\b(?:I(?:'|’)ll|I(?:'|’)m|I am|I will|Next I|Then I|I(?:'|’)ve got|I(?:'|’)ve confirmed)\b.{0,180}\b(?:inspect|checking|check|look|search|run|verify|edit|make|continue|pick|narrow)/i

const turnMarkers = ["HARNESS_PARITY_INSPECT_OK", "HARNESS_PARITY_EDIT_OK", "HARNESS_PARITY_VERIFY_OK"] as const

export const evaluateE4RealHarnessParity = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const [configRaw, stateRaw, eventsRaw, snapshotsRaw] = await Promise.all([
    readOptional(path.join(caseDir, "config.json")),
    readOptional(path.join(caseDir, "repl_state.ndjson")),
    readOptional(path.join(caseDir, "events.ndjson")),
    readOptional(path.join(caseDir, "pty_snapshots.txt")),
  ])

  const config = configRaw ? JSON.parse(configRaw) : {}
  if (!String(config.configPath ?? "").endsWith("agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml")) {
    anomalies.push({
      id: "wrong-config",
      message: `Expected GPT-5.4-mini Codex E4 live config, saw ${String(config.configPath ?? "<missing>")}.`,
    })
  }
  if (String(config.command ?? "") !== "bb repl") {
    anomalies.push({ id: "wrong-command", message: `Expected command "bb repl", saw ${String(config.command ?? "<missing>")}.` })
  }

  const states = parseJsonLines(stateRaw)
  const finalState = states.at(-1)?.state ?? null
  const model = String(finalState?.stats?.model ?? "")
  if (!model.includes("gpt-5.4-mini")) {
    anomalies.push({ id: "wrong-model", message: `Expected gpt-5.4-mini model in state dump, saw "${model}".` })
  }
  if (finalState?.pendingResponse !== false || finalState?.lastConversation?.phase !== "final") {
    anomalies.push({ id: "not-settled-final", message: "Final state dump did not settle to pendingResponse=false and final assistant phase." })
  }
  if (/^Halted\b/i.test(String(finalState?.status ?? "")) || finalState?.lastConversation?.speaker === "system") {
    anomalies.push({ id: "halted-final-state", message: `Final state ended halted/system instead of assistant-complete: ${String(finalState?.status ?? "<missing>")}` })
  }
  if ((finalState?.counts?.conversation ?? 0) < 6) {
    anomalies.push({ id: "conversation-too-short", message: `Expected at least 6 conversation entries, saw ${finalState?.counts?.conversation ?? 0}.` })
  }

  const stateHadSuccessfulTool = states.some((record) => record?.state?.lastToolEvent?.status === "success")
  if (!stateHadSuccessfulTool) {
    anomalies.push({ id: "no-successful-tool-state", message: "State dump never observed a successful tool event." })
  }

  const events = parseJsonLines(eventsRaw)
  const lifecyclePrompts = events
    .map((event) => event?.payload?.node?.payload?.payload?.user_prompt ?? event?.payload?.payload?.user_prompt)
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
  for (const prompt of lifecyclePrompts) {
    const markerCount = turnMarkers.reduce((count, marker) => count + (prompt.includes(marker) ? 1 : 0), 0)
    if (markerCount > 1 || /yet\.Now perform one small edit/.test(prompt)) {
      anomalies.push({
        id: "concatenated-user-turn-contract",
        message: "A backend task prompt contained multiple E4 turn contracts; interactive user turns must be submitted and executed independently.",
      })
      break
    }
  }
  const runFinishedCount = countMatchingEvents(events, (event) => event?.type === "run_finished")
  const completionCount = countMatchingEvents(events, (event) => event?.type === "completion")
  const toolishCount = countMatchingEvents(events, (event) => {
    const type = String(event?.type ?? "")
    const text = eventText(event)
    return type.includes("tool") || text.includes("shell_command") || text.includes("apply_patch")
  })
  if (runFinishedCount < 3) anomalies.push({ id: "missing-run-finished", message: `Expected at least 3 run_finished events, saw ${runFinishedCount}.` })
  if (completionCount < 3) anomalies.push({ id: "missing-completions", message: `Expected at least 3 completion events, saw ${completionCount}.` })
  if (toolishCount < 3) anomalies.push({ id: "missing-tool-events", message: `Expected at least 3 tool-like events, saw ${toolishCount}.` })

  const toolCallEvents = events.filter((event) => event?.type === "tool_call")
  const toolResultEvents = events.filter((event) => event?.type === "tool_result")
  if (toolCallEvents.length > 6 || toolResultEvents.length > 12) {
    anomalies.push({
      id: "excessive-tool-churn",
      message: `Expected bounded dummy-lane tool use, saw ${toolCallEvents.length} tool_call events and ${toolResultEvents.length} tool_result events.`,
    })
  }

  for (const segment of splitEventsByCompletion(events)) {
    const successfulToolIndex = segment.findIndex((event) => event?.type === "tool_result" && event?.payload?.success !== false && event?.payload?.error !== true)
    if (successfulToolIndex >= 0) {
      const afterSuccessfulTool = segment.slice(successfulToolIndex + 1)
      const postToolProgressTexts = new Set(
        afterSuccessfulTool
          .filter((event) => event?.type === "assistant_message")
          .map(eventText)
          .map((text) => text.trim().replace(/\s+/g, " "))
          .filter((text) => progressUpdatePattern.test(text)),
      )
      if (postToolProgressTexts.size >= 3) {
        anomalies.push({
          id: "progress-loop-after-tool",
          message: `Observed ${postToolProgressTexts.size} distinct Codex-style progress/status assistant messages after a successful tool result in one turn instead of finalizing the logical turn.`,
        })
        break
      }
    }
  }

  const explicitValidationKeys = new Set<string>()
  for (const event of events) {
    const payload = event?.payload ?? {}
    const nodePayload = payload?.node?.payload ?? {}
    const text = String(payload?.text ?? payload?.message?.content ?? nodePayload?.content ?? "")
    const turn = String(event?.turn ?? payload?.node?.turn ?? "")
    if (text.includes("<VALIDATION_ERROR>")) explicitValidationKeys.add(`${turn}:validation:${text.slice(0, 160)}`)
    if (nodePayload?.post_required_tool_extra_call_block) explicitValidationKeys.add(`${turn}:extra:${JSON.stringify(nodePayload.post_required_tool_extra_call_block)}`)
    if (nodePayload?.assistant_progress_update?.source === "tool_required_guard") {
      explicitValidationKeys.add(`${turn}:progress:${String(nodePayload.assistant_progress_update.text ?? "").slice(0, 160)}`)
    }
  }
  if (explicitValidationKeys.size > 9) {
      anomalies.push({
        id: "validation-loop",
        message: `Observed ${explicitValidationKeys.size} distinct validation/guard events; validation feedback must be bounded and non-looping.`,
      })
  }

  const commandEvents = events
    .map(commandFromEvent)
    .filter((command) => command.trim().length > 0)
  const parentSearchCommands = commandEvents.filter((command) =>
    /\bfind\s+\.\.|\brg\b.*\s\.\.|\bgrep\b.*\s\.\.|\bgit\s+status\b|\bAGENTS\.md\b/.test(command),
  )
  if (parentSearchCommands.length > 0) {
    anomalies.push({
      id: "parent-directory-search",
      message: `Dummy lane forbids parent/broad repo search; saw ${parentSearchCommands.length} offending command(s), first: ${parentSearchCommands[0]}`,
    })
  }

  const repoRoot = String(config.configPath ?? "").includes("/agent_configs/")
    ? String(config.configPath).split("/agent_configs/")[0]
    : path.resolve(caseDir, "../../../../..")
  const logMatch = eventsRaw.match(/file:\/\/(logging\/[A-Za-z0-9_.-]+)/)
  if (logMatch) {
    const compiledSystem = await readOptional(path.join(repoRoot, logMatch[1], "prompts/compiled_system.md"))
    if (!compiledSystem.includes("Codex CLI") || compiledSystem.trim().startsWith("../../../") || compiledSystem.includes("gpt_5_codex_prompt.md")) {
      anomalies.push({
        id: "system-prompt-not-loaded",
        message: `Compiled system prompt was not loaded from the Codex prompt artifact at ${path.join(repoRoot, logMatch[1], "prompts/compiled_system.md")}.`,
      })
    }
  } else if (finalState?.pendingResponse === false) {
    anomalies.push({ id: "missing-log-link", message: "No logging directory link was emitted, so compiled prompt parity could not be verified." })
  }

  for (const marker of turnMarkers) {
    if (!snapshotsRaw.includes(marker) && !eventsRaw.includes(marker) && !stateRaw.includes(marker)) {
      anomalies.push({ id: "missing-marker", message: `Missing assistant marker ${marker}.` })
    }
  }

  const visibleSurfaces = [snapshotsRaw, stateRaw]
  const promptLeakPattern = /You are GPT-5\.\d+ running in the Codex CLI|gpt_5_codex_prompt\.md|Your capabilities:\s*- Receive user prompts/i
  if (visibleSurfaces.some((surface) => promptLeakPattern.test(surface))) {
    anomalies.push({
      id: "prompt-leakage-visible",
      message: "Codex system prompt text or prompt artifact references appeared in user-visible/state surfaces.",
    })
  }

  const seenMarkers = turnMarkers.filter((marker) =>
    snapshotsRaw.includes(marker) || eventsRaw.includes(marker) || stateRaw.includes(marker),
  )
  if (seenMarkers.length > 0 && seenMarkers.length < 3) {
    anomalies.push({
      id: "turn-contract-not-preserved",
      message: `Only ${seenMarkers.length}/3 required dummy-lane turn markers appeared (${seenMarkers.join(", ")}).`,
    })
  }

  const commandCwd = String(config?.commandProvenance?.cwd ?? "")
  const caseInfoRaw = await readOptional(path.join(caseDir, "case_info.json"))
  const caseInfo = caseInfoRaw ? JSON.parse(caseInfoRaw) : {}
  const cwdRaw = String(caseInfo?.commandProvenance?.cwd ?? caseInfo?.cwd ?? caseInfo?.repro?.cwd ?? "")
  const fallbackWorkspace = path.resolve(os.tmpdir(), "bb_tmp_harness_parity_dummy/session_e4_real")
  const workspace = commandCwd.includes("tmp_harness_parity_dummy")
    ? path.resolve(commandCwd)
    : cwdRaw.includes("tmp_harness_parity_dummy")
      ? path.resolve(cwdRaw)
      : fallbackWorkspace
  const editedFile = path.join(workspace, "agent_notes.md")
  const editedRaw = await readOptional(editedFile)
  for (const marker of ["HARNESS_PARITY_EDIT_FILE", "ALPHA_WIDGET_CONFIRMED", "BETA_NOTE_CONFIRMED"]) {
    if (!editedRaw.includes(marker)) {
      anomalies.push({ id: "missing-file-marker", message: `Edited file ${editedFile} is missing ${marker}.` })
    }
  }

  if (!snapshotsRaw.includes("dummy-check-ok") && !eventsRaw.includes("dummy-check-ok")) {
    anomalies.push({ id: "missing-npm-check-output", message: "Verification turn did not preserve dummy-check-ok output." })
  }

  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateE4RealHarnessParity(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
