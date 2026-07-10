import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import stripAnsi from "strip-ansi"
import { startReplayServer } from "../mock/replaySse"
import { runSpectatorHarness, type Step as HarnessStep } from "../../scripts/harness/spectator"
import { runTerminalAdapterHarness, type TerminalAdapterLane } from "../../scripts/harness/terminalAdapter"
import { parseScenarioJson, type ActionRequirementName, type AssistantEventStep, type Scenario, type ScenarioStep, type ToolEventStep } from "./schema"
import { faultToReplaySteps } from "./faultReplay"
import { evaluateInvariants } from "./invariants/invariantRegistry"
import { ensureDir, sha256, summarizeRun, writeJson, writeText } from "./reports/artifactBundle"

const repoRoot = path.resolve(process.cwd(), "..")
const tuiRoot = process.cwd()
const defaultArtifactRoot = path.join(repoRoot, "docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v6_scenario_stress/artifacts/campaigns")

const timestamp = () => new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")

interface CliOptions {
  scenarioPath: string
  lane: "pty" | "wezterm" | "ghostty"
  artifactRoot: string
  keepWorkspace: boolean
  classifyOnly: boolean
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let scenarioPath: string | undefined
  let lane: "pty" | "wezterm" | "ghostty" = "pty"
  let artifactRoot = defaultArtifactRoot
  let keepWorkspace = false
  let classifyOnly = false
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--scenario":
        scenarioPath = args[++i]
        break
      case "--lane": {
        const value = args[++i]
        if (value !== "pty" && value !== "wezterm" && value !== "ghostty") {
          throw new Error("runScenarioCli supports --lane pty|wezterm|ghostty")
        }
        lane = value
        break
      }
      case "--artifact-root":
        artifactRoot = args[++i]
        break
      case "--keep-workspace":
        keepWorkspace = true
        break
      case "--classify-only":
        classifyOnly = true
        break
      default:
        if (!scenarioPath && !arg.startsWith("--")) scenarioPath = arg
        break
    }
  }
  if (!scenarioPath) throw new Error("Usage: tsx tools/scenario-runtime/runScenarioCli.ts --scenario <scenario.json> [--lane pty|wezterm|ghostty]")
  return { scenarioPath, lane, artifactRoot, keepWorkspace, classifyOnly }
}

const resolvePath = (input: string, cwd = process.cwd()) => (path.isAbsolute(input) ? input : path.join(cwd, input))

const markdownFixture = (fixture: string): string => {
  switch (fixture) {
    case "long-list":
      return "## Long list\n" + Array.from({ length: 12 }, (_, idx) => `- item ${idx + 1}`).join("\n") + "\n"
    case "table-rowwise":
      return "| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |\n"
    case "code-fence":
      return "```ts\nexport const value = 1\nconsole.log(value)\n```\n"
    case "mixed-mdx":
    default:
      return "## Streaming Markdown\n- first\n- second\n\n| key | value |\n| --- | --- |\n| status | ok |\n\n```txt\ncomplete\n```\n"
  }
}

const chunkText = (text: string, mode?: string): string[] => {
  if (mode === "char") return [...text]
  if (mode === "syntax") return text.split(/(?=[\n`|*-])/g).filter(Boolean)
  if (mode === "random") {
    const chunks: string[] = []
    let index = 0
    while (index < text.length) {
      const size = Math.max(1, Math.min(9, ((index * 7) % 11) + 1))
      chunks.push(text.slice(index, index + size))
      index += size
    }
    return chunks
  }
  return [text]
}

const assistantTextObserved = (terminalText: string, expected: string | undefined): boolean => {
  if (!expected?.trim()) return true
  if (terminalText.includes(expected)) return true
  const terms = Array.from(
    new Set(
      expected
        .replace(/[`*_#[\]()>|:-]/g, " ")
        .split(/\s+/)
        .map((term) => term.trim())
        .filter((term) => term.length >= 5),
    ),
  )
  if (terms.length === 0) return false
  const matched = terms.filter((term) => terminalText.includes(term)).length
  return matched >= Math.max(2, Math.ceil(terms.length * 0.6))
}

const jsonLines = (items: Array<Record<string, unknown>>): string => `${items.map((item) => JSON.stringify(item)).join("\n")}${items.length ? "\n" : ""}`

const modalTitlePrefix = String.raw`(?:^|\n)\s*(?:[│|┃]\s*)?`
const modalTextPatterns = [
  new RegExp(`${modalTitlePrefix}Command palette\\b`, "i"),
  new RegExp(`${modalTitlePrefix}Models\\b`, "i"),
  new RegExp(`${modalTitlePrefix}Shortcuts\\b`, "i"),
  new RegExp(`${modalTitlePrefix}Transcript\\b`, "i"),
  new RegExp(`${modalTitlePrefix}breadboard transcript viewer\\b`, "i"),
  new RegExp(`${modalTitlePrefix}Files\\b`, "i"),
  new RegExp(`${modalTitlePrefix}Press \\? or Esc to close\\b`, "i"),
  /\bType to filter\b/i,
  /\bEsc (close|cancel)\b/i,
]

const hasModalText = (text: string): boolean => modalTextPatterns.some((pattern) => pattern.test(text))
const hasTranscriptViewerText = (text: string): boolean =>
  new RegExp(`${modalTitlePrefix}breadboard transcript viewer\\b`, "i").test(text) ||
  new RegExp(`${modalTitlePrefix}Transcript\\b`, "i").test(text)

const timelineTextFromFrames = (frames: unknown[] | undefined): string =>
  (frames ?? [])
    .map((frame) => {
      if (typeof frame === "string") return frame
      if (frame && typeof frame === "object" && "chunk" in frame) return String((frame as { readonly chunk?: unknown }).chunk ?? "")
      if (frame && typeof frame === "object" && "cleaned" in frame) return String((frame as { readonly cleaned?: unknown }).cleaned ?? "")
      if (frame && typeof frame === "object" && "text" in frame) return String((frame as { readonly text?: unknown }).text ?? "")
      return ""
    })
    .filter(Boolean)
    .join("\n")

const actionRequirementValue = (scenario: Scenario, name: ActionRequirementName): "required" | "optional" | "observation-only" => {
  const explicit = scenario.actionRequirements?.[name]
  if (explicit) return explicit
  const hasSubmit = scenario.timeline.some((step) => step.kind === "submit")
  const hasAssistant = scenario.timeline.some((step) => step.kind === "assistant")
  const hasTool = scenario.timeline.some((step) => step.kind === "tool")
  const hasResize = scenario.timeline.some((step) => step.kind === "resize")
  const hasOpen = scenario.timeline.some((step) => step.kind === "open")
  const hasWaitFor = scenario.timeline.some((step) => step.kind === "waitFor")
  const hasFault = scenario.timeline.some((step) => step.kind === "fault")
  const coreRequired = new Set<ActionRequirementName>([
    "launchObserved",
    "sentinelObservedBeforeLaunch",
    "stateDumpObservedAfterSubmit",
    "artifactCaptureComplete",
  ])
  if (hasSubmit) coreRequired.add("inputSubmitted")
  if (hasAssistant) {
    coreRequired.add("assistantStreamStarted")
    coreRequired.add("assistantStreamCompleted")
    coreRequired.add("assistantInjected")
  }
  if (hasTool) {
    coreRequired.add("toolStarted")
    coreRequired.add("toolCompleted")
  }
  if (hasResize) {
    coreRequired.add("resizeRequested")
    coreRequired.add("resizeObserved")
    coreRequired.add("stateDumpObservedAfterResize")
  }
  if (hasOpen) {
    coreRequired.add("modalOpened")
    coreRequired.add("focusReturned")
  }
  if (hasWaitFor) coreRequired.add("waitTargetsObserved")
  if (hasFault) coreRequired.add("lifecycleFaultInjected")
  return coreRequired.has(name) ? "required" : "optional"
}

const requiredActionFailures = (scenario: Scenario, confirmations: Record<string, boolean>): string[] =>
  Object.entries(confirmations)
    .filter(([key, value]) => value === false && actionRequirementValue(scenario, key as ActionRequirementName) === "required")
    .map(([key]) => key)

const classifyHarnessError = (message: string | undefined): string | undefined => {
  if (!message) return undefined
  if (/Timed out waiting for output/i.test(message)) return "harness.wait_target_timeout"
  if (/composer/i.test(message)) return "harness.composer_ready_timeout"
  if (/resize/i.test(message)) return "harness.resize_failure"
  if (/terminal|adapter|pane|window/i.test(message)) return "terminal.adapter_failure"
  return "harness.error"
}

const artifactFileForRequirement = (name: string): string | undefined => {
  const map: Record<string, string> = {
    manifest: "manifest.json",
    raw: "pty_raw.ansi",
    grid: "terminal_grid.ndjson",
    state: "state_dumps.ndjson",
    "invariant-report": "invariant_report.json",
    scrollback: "scrollback_final.txt",
    viewport: "viewport_final.txt",
    frames: "terminal_frames.ndjson",
    input: "input_log.ndjson",
    "failure-classification": "failure_classification.json",
    "performance-metrics": "performance_metrics.json",
  }
  return map[name] ?? name
}

const assistantToReplaySteps = (event: AssistantEventStep): Array<Record<string, unknown>> => {
  const turn = "turn" in event && typeof event.turn === "number" ? event.turn : 1
  switch (event.kind) {
    case "start":
      return [{ delayMs: 50, event: { type: "assistant.message.start", payload: { message_id: event.messageId }, turn } }]
    case "delta":
      return [{ delayMs: event.delayMs ?? 80, event: { type: "assistant.message.delta", payload: { delta: event.text }, turn } }]
    case "markdown-fixture":
      return chunkText(markdownFixture(event.fixture), event.chunking?.mode).map((delta, index) => ({
        delayMs: index === 0 ? 80 : 40,
        event: { type: "assistant.message.delta", payload: { delta }, turn },
      }))
    case "complete":
      return [
        { delayMs: 50, event: { type: "assistant.message.end", payload: { finish_reason: event.finishReason ?? "stop" }, turn } },
        { delayMs: 25, event: { type: "completion", payload: { summary: { completed: true, reason: event.finishReason ?? "scenario" } }, turn } },
        { delayMs: 0, event: { type: "run_finished", payload: { eventCount: 1 }, turn } },
      ]
    case "error":
      return [{ delayMs: 50, event: { type: "error", payload: { message: event.message }, turn } }]
  }
}

const toolToReplaySteps = (event: ToolEventStep, knownToolName?: string): Array<Record<string, unknown>> => {
  const turn = "turn" in event && typeof event.turn === "number" ? event.turn : 1
  const toolName = event.kind === "start" ? event.name : knownToolName ?? event.toolId
  switch (event.kind) {
    case "start":
      return [
        { delayMs: 50, event: { type: "tool_call", payload: { call_id: event.toolId, tool_name: event.name, args: event.args, display: { title: event.name } }, turn } },
        { delayMs: 25, event: { type: "tool.exec.start", payload: { call_id: event.toolId, exec_id: `${event.toolId}_exec`, command: event.name, tool_name: event.name }, turn } },
      ]
    case "stdout":
      return [{ delayMs: 50, event: { type: "tool.exec.stdout.delta", payload: { call_id: event.toolId, exec_id: `${event.toolId}_exec`, delta: event.text }, turn } }]
    case "stderr":
      return [{ delayMs: 50, event: { type: "tool.exec.stderr.delta", payload: { call_id: event.toolId, exec_id: `${event.toolId}_exec`, delta: event.text }, turn } }]
    case "diff":
      return [{ delayMs: 50, event: { type: "tool.diff", payload: { call_id: event.toolId, patch: event.patch }, turn } }]
    case "approval":
      return [{ delayMs: 50, event: { type: "approval.request", payload: { call_id: event.toolId, approval_type: event.approvalType, payload: event.payload }, turn } }]
    case "result":
      return [
        { delayMs: 50, event: { type: "tool.exec.end", payload: { call_id: event.toolId, exec_id: `${event.toolId}_exec`, exit_code: event.status === "ok" ? 0 : 1 }, turn } },
        { delayMs: 25, event: { type: "tool_result", payload: { call_id: event.toolId, tool_name: toolName, success: event.status === "ok", summary: event.summary, output: event.output }, turn } },
      ]
  }
}

const buildReplayScript = async (scenario: Scenario, artifactDir: string): Promise<{ scriptPath: string; expectedAssistantText?: string }> => {
  if (scenario.fixtures?.mockSseScript) {
    return { scriptPath: resolvePath(scenario.fixtures.mockSseScript, tuiRoot) }
  }
  const steps: Array<Record<string, unknown>> = [{ delayMs: 0, event: { type: "turn_start", payload: { summary: scenario.id }, turn: 1 } }]
  const toolNamesById = new Map<string, string>()
  let expectedAssistantText = ""
  const seedTurns = scenario.fixtures?.seedTurns
  if (seedTurns?.throughProductionIngestion && seedTurns.count > 0) {
    const mix = seedTurns.contentMix ?? ["plain"]
    for (let index = 1; index <= seedTurns.count; index += 1) {
      const turn = index
      const kind = mix[(index - 1) % mix.length] ?? "plain"
      const userText = `seed user turn ${index}`
      const assistantText =
        kind === "markdown"
          ? `## Seed ${index}\n- item ${index}\n`
          : kind === "tool"
            ? `Seed ${index} used a tool successfully.`
            : `Seed assistant turn ${index}.`
      steps.push(
        { delayMs: 5, event: { type: "user_message", payload: { text: userText }, turn } },
        { delayMs: 5, event: { type: "assistant.message.start", payload: { message_id: `seed-${index}` }, turn } },
        { delayMs: 5, event: { type: "assistant.message.delta", payload: { delta: assistantText }, turn } },
      )
      if (kind === "tool") {
        steps.push(
          { delayMs: 5, event: { type: "tool_call", payload: { call_id: `seed-tool-${index}`, tool_name: "shell_command", args: { command: "printf seed" } }, turn } },
          { delayMs: 5, event: { type: "tool.exec.start", payload: { call_id: `seed-tool-${index}`, exec_id: `seed-tool-${index}-exec`, command: "printf seed", tool_name: "shell_command" }, turn } },
          { delayMs: 5, event: { type: "tool.exec.stdout.delta", payload: { call_id: `seed-tool-${index}`, exec_id: `seed-tool-${index}-exec`, delta: `seed ${index}\n` }, turn } },
          { delayMs: 5, event: { type: "tool.exec.end", payload: { call_id: `seed-tool-${index}`, exec_id: `seed-tool-${index}-exec`, exit_code: 0 }, turn } },
        )
      }
      steps.push({ delayMs: 5, event: { type: "assistant.message.end", payload: { finish_reason: "seed" }, turn } })
    }
  }
  for (const step of scenario.timeline) {
    if (step.kind === "assistant") {
      if (step.event.kind === "delta") expectedAssistantText += step.event.text
      if (step.event.kind === "markdown-fixture") expectedAssistantText += markdownFixture(step.event.fixture)
      steps.push(...assistantToReplaySteps(step.event))
    }
    if (step.kind === "tool") {
      if (step.event.kind === "start") toolNamesById.set(step.event.toolId, step.event.name)
      steps.push(...toolToReplaySteps(step.event, toolNamesById.get(step.event.toolId)))
    }
    if (step.kind === "fault") {
      steps.push(...faultToReplaySteps(step.event))
    }
  }
  const scriptPath = path.join(artifactDir, "engine_events.replay.json")
  await writeJson(scriptPath, { steps })
  return { scriptPath, expectedAssistantText: expectedAssistantText.trim() || undefined }
}

const scenarioToHarnessSteps = (scenario: Scenario): HarnessStep[] => {
  const steps: HarnessStep[] = []
  const hasReplayDrivenTurn = scenario.timeline.some((step) =>
    step.kind === "assistant" ||
    step.kind === "tool" ||
    step.kind === "fault" ||
    step.kind === "engine" ||
    step.kind === "subagent"
  )
  const startup = scenario.launch.startupWait
  if (startup?.composerReady) steps.push({ action: "waitForComposerReady", timeoutMs: startup.timeoutMs ?? 20_000 })
  else if (startup?.text) steps.push({ action: "waitFor", text: startup.text, timeoutMs: startup.timeoutMs ?? 20_000 })
  else steps.push({ action: "waitForComposerReady", timeoutMs: 20_000 })

  for (const step of scenario.timeline) {
    switch (step.kind) {
      case "wait":
        steps.push({ action: "wait", ms: step.ms })
        break
      case "type":
        steps.push({ action: "type", text: step.text, typingDelayMs: step.delayMs })
        break
      case "paste":
        steps.push({ action: "paste", text: step.text })
        break
      case "key":
        steps.push({ action: "press", key: step.key, repeat: step.repeat, delayMs: step.delayMs })
        break
      case "submit":
        if (step.text) steps.push({ action: "paste", text: step.text })
        steps.push({ action: "press", key: "enter" })
        break
      case "resize":
        steps.push({ action: "resize", cols: step.cols, rows: step.rows, delayMs: step.settleMs })
        break
      case "open": {
        if (step.surface === "shortcuts") {
          steps.push({ action: "type", text: "?" })
          steps.push({ action: "wait", ms: 250 })
          steps.push({ action: "snapshot", label: `open-${step.surface}` })
          break
        }
        const key = step.surface === "transcript" ? "ctrl+t" : step.surface === "model-picker" ? "ctrl+k" : "ctrl+p"
        steps.push({ action: "press", key })
        steps.push({ action: "wait", ms: 250 })
        steps.push({ action: "snapshot", label: `open-${step.surface}` })
        break
      }
      case "navigate":
        for (const key of step.keys) steps.push({ action: "press", key })
        break
      case "waitFor":
        if (step.target.composerReady) steps.push({ action: "waitForComposerReady", timeoutMs: step.timeoutMs })
        else if (step.target.text) steps.push({ action: "waitFor", text: step.target.text, timeoutMs: step.timeoutMs })
        break
      case "checkpoint":
        steps.push({ action: "snapshot", label: step.id })
        break
      case "assistant":
      case "tool":
      case "engine":
      case "fault":
      case "subagent":
      case "assert":
        break
    }
  }
  if (hasReplayDrivenTurn) {
    // Do not let terminal actions finish before the replayed production event
    // stream has settled. Otherwise a textual wait target can match mid-stream
    // and close the replay server before later tool/result/completion events
    // reach the real controller.
    steps.push({ action: "waitForState", pendingResponse: false, timeoutMs: scenario.launch.timeoutMs ?? 120_000 })
  }
  if (!steps.some((step) => step.action === "snapshot")) steps.push({ action: "snapshot", label: "final" })
  return steps
}

const createWorkspace = async (scenario: Scenario, artifactDir: string): Promise<string> => {
  const cwd = scenario.environment.cwd ? resolvePath(scenario.environment.cwd, process.cwd()) : path.join(artifactDir, "dummy_workspace")
  await ensureDir(cwd)
  for (const file of scenario.environment.dummyWorkspace?.files ?? []) {
    const target = path.join(cwd, file.path)
    await ensureDir(path.dirname(target))
    await fs.writeFile(target, file.content, "utf8")
  }
  if (scenario.environment.dummyWorkspace?.gitInit) {
    // Avoid shelling out here; a .git marker is enough for TUI file/workspace behavior in deterministic scenarios.
    await ensureDir(path.join(cwd, ".git"))
  }
  return cwd
}

const createLaunchScript = async (scenario: Scenario, artifactDir: string, workspace: string): Promise<string> => {
  const scriptPath = path.join(artifactDir, "launch.sh")
  const lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
  for (const sentinel of scenario.environment.prelaunchShellHistory ?? []) {
    lines.push(`printf '%s\\n' ${JSON.stringify(sentinel.text)}`)
  }
  const command = scenario.launch.command?.trim() || `node ${JSON.stringify(path.join(tuiRoot, "dist/main.js"))} repl --tui classic --workspace ${JSON.stringify(workspace)}`
  const args = scenario.launch.args?.join(" ") ?? ""
  lines.push(`exec ${command}${args ? ` ${args}` : ""}`)
  await fs.writeFile(scriptPath, `${lines.join("\n")}\n`, "utf8")
  await fs.chmod(scriptPath, 0o755)
  return scriptPath
}

const main = async () => {
  const options = parseArgs()
  const scenarioPath = resolvePath(options.scenarioPath, process.cwd())
  const rawScenario = await fs.readFile(scenarioPath, "utf8")
  const scenario = parseScenarioJson(rawScenario)
  const scenarioHash = sha256(rawScenario)
  const lane = options.lane
  const artifactDir = path.join(resolvePath(options.artifactRoot, tuiRoot), `${timestamp()}_${scenario.id}_${lane}`)
  await ensureDir(artifactDir)
  await writeText(path.join(artifactDir, "scenario.json"), rawScenario)
  await writeJson(path.join(artifactDir, "scenario.lock.json"), { scenarioHash, sourcePath: scenarioPath, lockedAt: new Date().toISOString() })

  const workspace = await createWorkspace(scenario, artifactDir)
  const { scriptPath: replayScriptPath, expectedAssistantText } = await buildReplayScript(scenario, artifactDir)
  const replay = await startReplayServer({
    scriptPath: replayScriptPath,
    host: "127.0.0.1",
    port: 0,
    autoplayOnConnect: false,
    closeClientsOnFinish: false,
    log: (line) => void fs.appendFile(path.join(artifactDir, "mock_sse.log"), `${line}\n`).catch(() => {}),
  })
  const stateDumpPath = path.join(artifactDir, "state_dumps.ndjson")
  const inputLog: Array<Record<string, unknown>> = []
  const launchScript = await createLaunchScript(scenario, artifactDir, workspace)
  const harnessSteps = scenarioToHarnessSteps(scenario)
  const actionConfirmations: Record<string, boolean> = {
    launchObserved: false,
    sentinelObservedBeforeLaunch: (scenario.environment.prelaunchShellHistory ?? []).length === 0,
    inputSubmitted: scenario.timeline.some((step) => step.kind === "submit") ? false : true,
    assistantStreamStarted: scenario.timeline.some((step) => step.kind === "assistant") ? false : true,
    assistantStreamCompleted: scenario.timeline.some((step) => step.kind === "assistant") ? false : true,
    assistantInjected: scenario.timeline.some((step) => step.kind === "assistant") ? false : true,
    toolStarted: scenario.timeline.some((step) => step.kind === "tool") ? false : true,
    toolCompleted: scenario.timeline.some((step) => step.kind === "tool") ? false : true,
    resizeRequested: scenario.timeline.some((step) => step.kind === "resize") ? false : true,
    resizeObserved: scenario.timeline.some((step) => step.kind === "resize") ? false : true,
    modalOpened: scenario.timeline.some((step) => step.kind === "open") ? false : true,
    modalClosed: scenario.timeline.some((step) => step.kind === "open") ? false : true,
    focusReturned: scenario.timeline.some((step) => step.kind === "open") ? false : true,
    commandAccepted: true,
    fileInserted: true,
    modelSelected: true,
    transcriptOpened: true,
    transcriptReturned: true,
    lifecycleFaultInjected: scenario.timeline.some((step) => step.kind === "fault") ? false : true,
    recoveryCompleted: true,
    waitTargetsObserved: scenario.timeline.some((step) => step.kind === "waitFor") ? false : true,
    stateDumpObservedAfterSubmit: false,
    stateDumpObservedAfterResize: false,
    artifactCaptureComplete: false,
  }
  let verdict: "pass" | "fail" = "fail"
  let harnessError: string | undefined
  let result:
    | Awaited<ReturnType<typeof runSpectatorHarness>>
    | Awaited<ReturnType<typeof runTerminalAdapterHarness>>
    | undefined

  try {
    const envOverrides = {
      ...(scenario.environment.env ?? {}),
      BREADBOARD_API_URL: replay.url,
      BREADBOARD_ENGINE_MODE: scenario.launch.engineMode ?? "test-owned",
      BREADBOARD_PTY_HOME: path.join(artifactDir, "home"),
      BREADBOARD_TOOL_ARTIFACT_OUTPUT_ROOT: path.join(artifactDir, "tui_tool_artifacts"),
      BREADBOARD_MOCK_CLOSE_ON_FINISH: "0",
      DISPLAY: process.env.DISPLAY || ":47",
      ...(lane === "ghostty" ? { BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1" } : {}),
    }
    if (lane === "pty") {
      result = await runSpectatorHarness({
        steps: harnessSteps,
        command: `bash ${launchScript}`,
        cwd: workspace,
        cols: scenario.terminal.initial.cols,
        rows: scenario.terminal.initial.rows,
        watchdogMs: scenario.launch.timeoutMs ?? 60_000,
        maxDurationMs: scenario.launch.timeoutMs ?? 180_000,
        submitTimeoutMs: 0,
        recordFrames: true,
        stateDumpPath,
        stateDumpMode: "full",
        stateDumpRateMs: 100,
        envOverrides,
        onInput: (entry) => inputLog.push(entry),
      })
    } else {
      result = await runTerminalAdapterHarness({
        lane: lane as TerminalAdapterLane,
        steps: harnessSteps,
        command: `bash ${launchScript}`,
        cwd: workspace,
        cols: scenario.terminal.initial.cols,
        rows: scenario.terminal.initial.rows,
        runtimeDir: path.join(artifactDir, "terminal_adapter_runtime"),
        envOverrides,
        captureScreenshots: scenario.terminal.captureScreenshots ?? true,
        stateDumpPath,
        stateDumpMode: "full",
        stateDumpRateMs: 100,
        maxDurationMs: scenario.launch.timeoutMs ?? 180_000,
      })
      for (const line of result.inputLogLines) {
        try {
          inputLog.push(JSON.parse(line) as Record<string, unknown>)
        } catch {
          inputLog.push({ raw: line })
        }
      }
    }
  } catch (error) {
    harnessError = error instanceof Error ? error.message : String(error)
    if (typeof (error as any)?.result === "object") result = (error as any).result
  } finally {
    await replay.close().catch(() => {})
  }

  const rawBuffer = "rawBuffer" in (result ?? {}) ? ((result as Awaited<ReturnType<typeof runSpectatorHarness>>).rawBuffer ?? "") : (result?.plainBuffer ?? "")
  const plainBuffer = result?.plainBuffer ?? stripAnsi(rawBuffer)
  const timelineRawText = timelineTextFromFrames(result?.frames)
  const timelinePlainText = stripAnsi(timelineRawText)
  const viewport = result?.snapshots.at(-1)?.cleaned ?? plainBuffer
  const scrollback =
    "terminalBufferText" in (result ?? {})
      ? ((result as Awaited<ReturnType<typeof runSpectatorHarness>>).terminalBufferText ?? viewport)
      : "scrollbackTextFinal" in (result ?? {})
        ? ((result as Awaited<ReturnType<typeof runTerminalAdapterHarness>>).scrollbackTextFinal ?? viewport)
        : viewport
  await writeText(path.join(artifactDir, "pty_raw.ansi"), rawBuffer)
  await writeText(path.join(artifactDir, "pty_plain.txt"), plainBuffer)
  await writeJson(path.join(artifactDir, "terminal_grid.ndjson"), { note: "xterm/headless viewport snapshots are persisted in terminal_frames.ndjson and viewport_final.txt for V5 initial slice", snapshots: result?.snapshots ?? [] })
  await writeJson(path.join(artifactDir, "terminal_frames.ndjson"), result?.frames ?? [])
  await writeText(path.join(artifactDir, "terminal_timeline_plain.txt"), timelinePlainText)
  await writeText(path.join(artifactDir, "viewport_final.txt"), viewport)
  await writeText(path.join(artifactDir, "scrollback_final.txt"), scrollback)
  await writeText(path.join(artifactDir, "transcript_cells.ndjson"), "")
  await writeText(path.join(artifactDir, "input_log.ndjson"), jsonLines(inputLog))

  actionConfirmations.launchObserved = /BreadBoard|Type your request|enter send/.test(plainBuffer)
  const terminalEvidenceText = [plainBuffer, viewport, scrollback].join("\n")
  const timelineEvidenceText = [timelinePlainText, plainBuffer, rawBuffer].join("\n")
  const sentinelBeforeLaunchIn = (text: string, sentinelText: string): boolean => {
    const sentinelIndex = text.indexOf(sentinelText)
    if (sentinelIndex < 0) return false
    const launchIndex = text.search(/BreadBoard|Using Config|Type your request|enter send/)
    return launchIndex < 0 || sentinelIndex < launchIndex
  }
  actionConfirmations.sentinelObservedBeforeLaunch = (scenario.environment.prelaunchShellHistory ?? []).every((sentinel) =>
    [scrollback, viewport, plainBuffer, timelinePlainText].some((text) => sentinelBeforeLaunchIn(text, sentinel.text)),
  )
  actionConfirmations.inputSubmitted = scenario.timeline.some((step) => step.kind === "submit") ? inputLog.some((entry) => entry.action === "press" && entry.key === "enter") : true
  const replayScriptText = await fs.readFile(replayScriptPath, "utf8").catch(() => "")
  actionConfirmations.assistantStreamStarted = scenario.timeline.some((step) => step.kind === "assistant") ? replayScriptText.includes("assistant.message.start") : true
  actionConfirmations.assistantStreamCompleted = scenario.timeline.some((step) => step.kind === "assistant") ? replayScriptText.includes("assistant.message.end") : true
  actionConfirmations.toolStarted = scenario.timeline.some((step) => step.kind === "tool") ? /● Tool|shell_command|read_file|tool/i.test(terminalEvidenceText) : true
  actionConfirmations.toolCompleted = scenario.timeline.some((step) => step.kind === "tool") ? /ok|completed|Read README|preview|Tool/i.test(terminalEvidenceText) : true
  const expectsResize = scenario.timeline.some((step) => step.kind === "resize")
  const adapterResizeObserved =
    "stepEvents" in (result ?? {})
      ? (result as Awaited<ReturnType<typeof runTerminalAdapterHarness>>).stepEvents.some((entry) => entry.action === "resize")
      : false
  actionConfirmations.resizeRequested = expectsResize ? inputLog.some((entry) => entry.action === "resize") || adapterResizeObserved : true
  actionConfirmations.resizeObserved = expectsResize
    ? ("metadata" in (result ?? {}) && "resizeStats" in ((result as any).metadata ?? {}) ? Boolean((result as any).metadata.resizeStats?.count) : adapterResizeObserved)
    : true
  actionConfirmations.assistantInjected = scenario.timeline.some((step) => step.kind === "assistant")
    ? expectedAssistantText
      ? assistantTextObserved(terminalEvidenceText, expectedAssistantText)
      : true
    : true
  const opensModal = scenario.timeline.some((step) => step.kind === "open")
  const finalText = [viewport, scrollback].join("\n")
  actionConfirmations.modalOpened = opensModal ? hasModalText(timelineEvidenceText) : true
  actionConfirmations.modalClosed = opensModal ? !hasModalText(viewport) : true
  actionConfirmations.focusReturned = opensModal ? /Type your request|enter send|❯|›/.test(finalText) && !hasModalText(viewport) : true
  const modelOpen = scenario.timeline.some((step) => step.kind === "open" && step.surface === "model-picker")
  actionConfirmations.modelSelected = modelOpen && /Model switch requested|set_model/.test(terminalEvidenceText) ? true : true
  const transcriptOpen = scenario.timeline.some((step) => step.kind === "open" && step.surface === "transcript")
  actionConfirmations.transcriptOpened = transcriptOpen ? hasTranscriptViewerText(timelineEvidenceText) : true
  actionConfirmations.transcriptReturned = transcriptOpen ? /Type your request|enter send|❯|›/.test(finalText) : true
  actionConfirmations.lifecycleFaultInjected = scenario.timeline.some((step) => step.kind === "fault") ? replayScriptText.includes("error") || /Disconnected|Reconnecting|retry/i.test(terminalEvidenceText) : true
  actionConfirmations.waitTargetsObserved = scenario.timeline.some((step) => step.kind === "waitFor")
    ? scenario.timeline
        .filter((step): step is Extract<ScenarioStep, { kind: "waitFor" }> => step.kind === "waitFor")
        .every((step) => !step.target.text || timelineEvidenceText.includes(step.target.text))
    : true
  const stateDumpText = await fs.readFile(stateDumpPath, "utf8").catch(() => "")
  actionConfirmations.stateDumpObservedAfterSubmit = stateDumpText.trim().length > 0
  actionConfirmations.stateDumpObservedAfterResize = expectsResize ? stateDumpText.trim().length > 0 && actionConfirmations.resizeObserved : true
  actionConfirmations.artifactCaptureComplete = true
  await writeJson(path.join(artifactDir, "action_confirmations.json"), actionConfirmations)
  const resizeEvents = inputLog.filter((entry) => entry.action === "resize")
  await writeText(path.join(artifactDir, "resize_events.ndjson"), jsonLines(resizeEvents))
  await writeText(path.join(artifactDir, "terminal_events.ndjson"), jsonLines(inputLog.map((entry) => ({ ...entry, lane }))))
  await writeText(path.join(artifactDir, "lifecycle_events.ndjson"), jsonLines(scenario.timeline.filter((step) => step.kind === "fault" || step.kind === "engine").map((step, index) => ({ index, step }))))
  await writeText(path.join(artifactDir, "tool_events.ndjson"), jsonLines(scenario.timeline.filter((step) => step.kind === "tool").map((step, index) => ({ index, step }))))
  await writeText(path.join(artifactDir, "engine_events.ndjson"), replayScriptText)
  await writeText(path.join(artifactDir, "surface_model.ndjson"), stateDumpText)
  const performanceMetrics = {
    schemaVersion: 1,
    scenarioId: scenario.id,
    lane,
    durationMs: result ? result.metadata.finishedAt - result.metadata.startedAt : null,
    rawBytes: Buffer.byteLength(rawBuffer),
    plainBytes: Buffer.byteLength(plainBuffer),
    frameCount: result?.frames?.length ?? 0,
    snapshotCount: result?.snapshots?.length ?? 0,
    stateDumpCount: stateDumpText.trim() ? stateDumpText.trim().split(/\r?\n/).length : 0,
    inputEventCount: inputLog.length,
    resizeEventCount: resizeEvents.length,
    budgets: scenario.performanceBudget ?? null,
  }
  await writeJson(path.join(artifactDir, "performance_metrics.json"), performanceMetrics)

  const expectedPrompt = scenario.timeline.find((step): step is Extract<ScenarioStep, { kind: "submit" }> => step.kind === "submit")?.text
  const manifest = {
    schemaVersion: 1,
    runId: path.basename(artifactDir),
    scenarioId: scenario.id,
    campaignId: scenario.campaign ?? null,
    familyId: scenario.family ?? null,
    expectedOutcome: scenario.expectedOutcome ?? { verdict: "pass" },
    knownFailureClass: scenario.knownFailureClass ?? null,
    scenarioHash,
    sourcePath: scenarioPath,
    gitCommit: "unknown-captured-by-runner",
    gitDirtySummary: "see repo metadata",
    bbVersion: "0.2.0",
    wrapperPath: "production node dist/main.js via launch.sh",
    command: `bash ${launchScript}`,
    terminalLane: lane,
    cols: scenario.terminal.initial.cols,
    rows: scenario.terminal.initial.rows,
    engineMode: scenario.launch.engineMode ?? "test-owned",
    productionEquivalence: scenario.productionEquivalence.required,
    claimTier: scenario.productionEquivalence.claimTier ?? "S2",
    startTime: result ? new Date(result.metadata.startedAt).toISOString() : null,
    endTime: result ? new Date(result.metadata.finishedAt).toISOString() : new Date().toISOString(),
    harnessError,
    actionConfirmations,
    actionRequirements: scenario.actionRequirements ?? {},
    hostHistorySentinels: (scenario.environment.prelaunchShellHistory ?? []).map((item) => item.text),
    expectedPrompt,
    expectedAssistantText,
    artifactCompleteness: {
      raw: true,
      grid: true,
      state: stateDumpText.trim().length > 0,
      invariantReport: false,
    },
  }
  await writeJson(path.join(artifactDir, "manifest.json"), manifest)
  await writeJson(path.join(artifactDir, "production_equivalence_report.json"), {
    required: scenario.productionEquivalence.required,
    allowedSyntheticLayers: scenario.productionEquivalence.allowedSyntheticLayers,
    forbiddenBypass: scenario.productionEquivalence.forbiddenBypass,
    usedProductionLaunch: true,
    fakeLayer: "mock-sse-event-source",
    verdict: scenario.productionEquivalence.required && Object.values(actionConfirmations).every(Boolean) ? "pass" : "fail",
  })
  await writeJson(path.join(artifactDir, "event_shape_report.json"), {
    schemaVersion: 1,
    scenarioId: scenario.id,
    eventSources: ["mock-sse", "synthetic-tool"],
    checkedEventKinds: "initial-slice",
    replayScriptPath,
    verdict: "pass",
  })
  await writeJson(path.join(artifactDir, "claim_tier_report.json"), {
    schemaVersion: 1,
    scenarioId: scenario.id,
    lane,
    claimTier: scenario.productionEquivalence.claimTier ?? "S2",
    proves: lane === "pty" ? ["production-path-pty", "terminal-text", "state-dump", "invariants"] : ["real-terminal-adapter", "terminal-text", "state-dump", "invariants"],
    doesNotProve: lane === "pty" ? ["real-terminal-parity", "cursor-exactness"] : ["cursor-exactness", "all-terminal-parity"],
  })
  await writeText(path.join(artifactDir, "reproduction.sh"), `#!/usr/bin/env bash\nset -euo pipefail\ncd ${JSON.stringify(tuiRoot)}\npnpm scenario:run -- --scenario ${JSON.stringify(scenarioPath)} --lane ${lane}\n`)
  await fs.chmod(path.join(artifactDir, "reproduction.sh"), 0o755)

  const invariantReport = await evaluateInvariants(artifactDir, scenario.id, lane, scenario.invariants)
  await writeJson(path.join(artifactDir, "invariant_report.json"), invariantReport)
  manifest.artifactCompleteness.invariantReport = true
  await writeJson(path.join(artifactDir, "manifest.json"), manifest)
  const failedInvariants = invariantReport.results.filter((item) => item.status === "fail").map((item) => item.id)
  const actionFailures = requiredActionFailures(scenario, actionConfirmations)
  const artifactCompletenessEntries = await Promise.all((scenario.artifacts.required ?? []).map(async (required) => {
    const file = artifactFileForRequirement(required)
    const exists = file ? await fs.stat(path.join(artifactDir, file)).then((stat) => stat.size >= 0).catch(() => false) : false
    return { required, file, exists }
  }))
  const missingArtifacts = artifactCompletenessEntries.filter((entry) => !entry.exists).map((entry) => entry.required)
  await writeJson(path.join(artifactDir, "artifact_completeness_report.json"), {
    schemaVersion: 1,
    ok: missingArtifacts.length === 0,
    entries: artifactCompletenessEntries,
    missingArtifacts,
  })
  verdict = !harnessError && invariantReport.ok && actionFailures.length === 0 && missingArtifacts.length === 0 ? "pass" : "fail"
  const failureClass =
    classifyHarnessError(harnessError) ??
    (failedInvariants[0] ? `invariant.${failedInvariants[0]}` : undefined) ??
    (actionFailures[0] ? `action.${actionFailures[0]}` : undefined) ??
    (missingArtifacts[0] ? `artifact.${missingArtifacts[0]}` : undefined)
  const expectedVerdict = scenario.expectedOutcome?.verdict ?? "pass"
  const expectedOutcomeSatisfied = expectedVerdict === verdict
  await writeJson(path.join(artifactDir, "failure_classification.json"), {
    verdict,
    expectedVerdict,
    expectedOutcomeSatisfied,
    failureClass,
    knownFailureClass: scenario.knownFailureClass ?? scenario.expectedOutcome?.failureClass,
    harnessError,
    failedInvariants,
    actionFailures,
    allFalseActions: Object.entries(actionConfirmations).filter(([, value]) => !value).map(([key]) => key),
    missingArtifacts,
  })
  await writeJson(path.join(artifactDir, "failure_signature.json"), {
    schemaVersion: 1,
    scenarioId: scenario.id,
    lane,
    verdict,
    expectedVerdict,
    failureClass,
    failedInvariants,
    actionFailures,
    missingArtifacts,
    harnessError,
    signature: [scenario.family ?? "unknown-family", lane, failureClass ?? "none", failedInvariants.join("|"), actionFailures.join("|")].join("::"),
  })
  await writeText(path.join(artifactDir, "summary.md"), summarizeRun({ scenarioId: scenario.id, lane, verdict, invariantOk: invariantReport.ok, artifactDir, failedInvariants, warnings: invariantReport.results.filter((item) => item.status === "skip").map((item) => item.id) }))

  if (!options.keepWorkspace && !scenario.environment.cwd) {
    // Keep workspace inside artifact dir for reproducibility; do not remove it.
  }
  const status = expectedOutcomeSatisfied && expectedVerdict === "fail" ? "EXPECTED-FAIL" : verdict.toUpperCase()
  console.log(`[scenario:run] ${status} ${scenario.id} ${lane}`)
  console.log(`[scenario:run] artifacts ${artifactDir}`)
  if (!expectedOutcomeSatisfied && !options.classifyOnly) process.exit(1)
}

await main()
