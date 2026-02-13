import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { evaluateTranscriptNoiseGate } from "../src/commands/repl/transcriptNoiseGate.js"
import { buildSubagentStripSummary } from "../src/repl/components/replView/controller/subagentStrip.js"

type Controller = {
  runtimeFlags: Record<string, unknown>
  applyEvent: (evt: any) => void
  getState: () => any
}

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  timestamp: seq,
  payload,
})

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

const createController = (): Controller =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as Controller

const summarizeState = (state: any) => {
  const taskToolRailLines = state.toolEvents.filter((entry: any) => String(entry.text ?? "").startsWith("[task]")).length
  const subagentSlots = state.liveSlots.filter((slot: any) => String(slot.text ?? "").includes("[subagent]"))
  const strip = buildSubagentStripSummary(state.workGraph)
  const noise = evaluateTranscriptNoiseGate(state, 0.8)
  return {
    taskToolRailLines,
    subagentSlotCount: subagentSlots.length,
    subagentSlots: subagentSlots.map((slot: any) => ({
      text: String(slot.text ?? ""),
      status: String(slot.status ?? ""),
      createdAt: Number(slot.createdAt ?? 0),
      expiresAt: Number(slot.expiresAt ?? 0),
    })),
    workGraphOrder: state.workGraph.itemOrder,
    workGraphStatuses: state.workGraph.itemOrder.map((workId: string) => ({
      id: workId,
      status: String(state.workGraph.itemsById?.[workId]?.status ?? "unknown"),
    })),
    stripSummary: strip,
    noise,
  }
}

const runAsyncCompletionScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
    subagentToastsEnabled: true,
  }
  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp1-async",
      mode: "async",
      status: "running",
      description: "Resolve dependencies",
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp1-async",
      mode: "async",
      status: "completed",
      description: "Resolve dependencies",
    }),
  )
  return {
    scenario: "cp1_async_completion",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      noTaskToolRailSpam: summarizeState(controller.getState()).taskToolRailLines === 0,
      completedToastPresent: summarizeState(controller.getState()).subagentSlots.some((slot) =>
        slot.text.includes("[subagent] completed"),
      ),
      completedStatusInWorkGraph: summarizeState(controller.getState()).workGraphStatuses.some(
        (entry) => entry.id === "task-cp1-async" && entry.status === "completed",
      ),
    },
    state: summarizeState(controller.getState()),
  }
}

const runFailureStickyScenario = async () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
    subagentToastsEnabled: true,
  }
  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp1-fail",
      mode: "async",
      status: "running",
      description: "Parse logs",
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp1-fail",
      mode: "async",
      status: "failed",
      description: "Parse logs",
    }),
  )
  const immediate = summarizeState(controller.getState())
  await sleep(2_100)
  const mid = summarizeState(controller.getState())
  await sleep(2_400)
  const expired = summarizeState(controller.getState())
  return {
    scenario: "cp1_failure_sticky_toast",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      failedToastInitiallyPresent: immediate.subagentSlots.some((slot) => slot.text.includes("[subagent] failed")),
      failedToastStillPresentAt2s: mid.subagentSlots.some((slot) => slot.text.includes("[subagent] failed")),
      failedToastExpiredBy4_5s: !expired.subagentSlots.some((slot) => slot.text.includes("[subagent] failed")),
    },
    immediate,
    midAfter2_1s: mid,
    after4_5s: expired,
  }
}

const runAsciiNoColorFallbackScenario = async () => {
  const prior = {
    noColor: process.env.NO_COLOR,
    ascii: process.env.BREADBOARD_ASCII,
    asciiOnly: process.env.BREADBOARD_TUI_ASCII_ONLY,
  }
  process.env.NO_COLOR = "1"
  process.env.BREADBOARD_ASCII = "1"
  process.env.BREADBOARD_TUI_ASCII_ONLY = "1"
  try {
    const { resolveTuiConfig } = await import("../src/tui_config/load.js")
    const resolved = await resolveTuiConfig({
      workspace: ".",
      cliStrict: true,
      colorAllowed: true,
    })
    const controller = createController()
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      subagentWorkGraphEnabled: true,
      subagentCoalesceMs: 0,
      subagentToastsEnabled: true,
    }
    controller.applyEvent(
      event(1, "task_event", {
        task_id: "task-cp1-ascii",
        mode: "async",
        status: "completed",
        description: "Ascii fallback",
      }),
    )
    const state = summarizeState(controller.getState())
    const slotTexts = state.subagentSlots.map((slot) => slot.text)
    return {
      scenario: "cp1_ascii_no_color_fallback",
      generatedAt: new Date().toISOString(),
      acceptanceChecks: {
        asciiOnlyResolved: resolved.display.asciiOnly === true,
        colorModeResolvedNone: resolved.display.colorMode === "none",
        subagentToastRendered: slotTexts.some((text) => text.includes("[subagent]")),
      },
      config: {
        asciiOnly: resolved.display.asciiOnly,
        colorMode: resolved.display.colorMode,
      },
      state,
    }
  } finally {
    if (prior.noColor == null) delete process.env.NO_COLOR
    else process.env.NO_COLOR = prior.noColor
    if (prior.ascii == null) delete process.env.BREADBOARD_ASCII
    else process.env.BREADBOARD_ASCII = prior.ascii
    if (prior.asciiOnly == null) delete process.env.BREADBOARD_TUI_ASCII_ONLY
    else process.env.BREADBOARD_TUI_ASCII_ONLY = prior.asciiOnly
  }
}

const main = async (): Promise<void> => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const asyncCapture = runAsyncCompletionScenario()
  const failureCapture = await runFailureStickyScenario()
  const asciiCapture = await runAsciiNoColorFallbackScenario()
  const captures = [asyncCapture, failureCapture, asciiCapture]
  for (const capture of captures) {
    const filePath = path.join(outputDir, `${capture.scenario}_capture_20260213.json`)
    writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
    console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
  }
}

void main()
