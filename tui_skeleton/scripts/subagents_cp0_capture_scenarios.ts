import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
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

const createController = (): Controller =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as Controller

const runFlagsOffScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: false,
    subagentStripEnabled: false,
    subagentToastsEnabled: false,
    subagentTaskboardEnabled: false,
    subagentFocusEnabled: false,
    subagentCoalesceMs: 0,
  }

  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp0-flags-off",
      mode: "async",
      status: "running",
      description: "Flags-off baseline",
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp0-flags-off",
      mode: "async",
      status: "completed",
      description: "Flags-off baseline",
      output_excerpt: "ok",
    }),
  )

  const state = controller.getState()
  const taskToolRailLines = state.toolEvents.filter((entry: any) => String(entry.text ?? "").startsWith("[task]")).length
  const subagentSlotCount = state.liveSlots.filter((slot: any) => String(slot.text ?? "").includes("[subagent]")).length
  const stripSummary = buildSubagentStripSummary(state.workGraph)
  const workItemCount = Array.isArray(state.workGraph?.itemOrder) ? state.workGraph.itemOrder.length : 0

  return {
    scenario: "cp0_flags_off_baseline",
    generatedAt: new Date().toISOString(),
    gatePolicy: {
      allowTaskToolRailLines: true,
    },
    acceptanceChecks: {
      taskEventsRouteToToolRail: taskToolRailLines > 0,
      noSubagentToasts: subagentSlotCount === 0,
      workGraphUnchangedWhenDisabled: workItemCount === 0,
      stripSummaryAbsentWhenNoWorkItems: stripSummary == null,
    },
    state: {
      taskToolRailLines,
      subagentSlotCount,
      workItemCount,
      stripSummary,
      runtimeFlags: {
        subagentWorkGraphEnabled: controller.runtimeFlags.subagentWorkGraphEnabled,
        subagentStripEnabled: controller.runtimeFlags.subagentStripEnabled,
        subagentToastsEnabled: controller.runtimeFlags.subagentToastsEnabled,
        subagentTaskboardEnabled: controller.runtimeFlags.subagentTaskboardEnabled,
        subagentFocusEnabled: controller.runtimeFlags.subagentFocusEnabled,
      },
    },
  }
}

const main = (): void => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const capture = runFlagsOffScenario()
  const filePath = path.join(outputDir, `${capture.scenario}_capture_20260213.json`)
  writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
  console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
}

main()
