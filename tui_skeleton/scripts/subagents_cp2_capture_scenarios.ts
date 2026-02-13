import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { countTaskRowsByStatusGroup, formatTaskStepLine } from "../src/repl/components/replView/controller/taskboardStatus.js"

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

const summarizeTaskboard = (state: any) => {
  const rows = state.workGraph.itemOrder.map((id: string) => {
    const item = state.workGraph.itemsById[id]
    return {
      id,
      status: String(item?.status ?? "pending"),
      laneId: String(item?.laneId ?? "primary"),
      mode: String(item?.mode ?? "unknown"),
      counters: item?.counters ?? { completed: 0, running: 0, failed: 0, total: 0 },
      steps:
        Array.isArray(item?.steps) && item.steps.length > 0
          ? item.steps.map((step: any) => formatTaskStepLine(step))
          : [],
    }
  })
  return {
    itemCount: rows.length,
    countsByStatus: countTaskRowsByStatusGroup(rows),
    rows,
  }
}

const runSyncProgressionScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
  }
  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp2-sync",
      mode: "sync",
      status: "running",
      description: "Apply patch",
      tool_name: "apply_patch",
      call_id: "call-sync-1",
      attempt: 1,
      output_excerpt: "started",
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp2-sync",
      mode: "sync",
      status: "completed",
      description: "Apply patch",
      tool_name: "apply_patch",
      call_id: "call-sync-1",
      attempt: 1,
      output_excerpt: "done",
    }),
  )
  const state = controller.getState()
  const summary = summarizeTaskboard(state)
  return {
    scenario: "cp2_sync_progression",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      hasSingleTask: summary.itemCount === 1,
      terminalCompleted: summary.rows[0]?.status === "completed",
      stepGrammarPresent: (summary.rows[0]?.steps.length ?? 0) > 0,
      syncBadgeModeRecorded: summary.rows[0]?.mode === "sync",
    },
    summary,
  }
}

const runAsyncWakeupScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
  }
  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp2-async",
      mode: "async",
      status: "pending",
      description: "Wait for upstream output",
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp2-async",
      mode: "async",
      status: "blocked",
      description: "Wait for upstream output",
      output_excerpt: "wakeup pending",
    }),
  )
  controller.applyEvent(
    event(3, "task_event", {
      task_id: "task-cp2-async",
      mode: "async",
      status: "running",
      description: "Process upstream output",
      tool_name: "parse_output",
      call_id: "call-async-1",
      attempt: 1,
    }),
  )
  controller.applyEvent(
    event(4, "task_event", {
      task_id: "task-cp2-async",
      mode: "async",
      status: "completed",
      description: "Process upstream output",
      tool_name: "parse_output",
      call_id: "call-async-1",
      attempt: 1,
      output_excerpt: "output-ready",
    }),
  )
  const summary = summarizeTaskboard(controller.getState())
  return {
    scenario: "cp2_async_wakeup_output_ready",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      terminalCompleted: summary.rows[0]?.status === "completed",
      asyncModeRecorded: summary.rows[0]?.mode === "async",
      hasChecklistRows: (summary.rows[0]?.steps.length ?? 0) > 0,
    },
    summary,
  }
}

const runRetryChainScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
  }
  controller.applyEvent(
    event(1, "task_event", {
      task_id: "task-cp2-retry",
      mode: "async",
      status: "running",
      description: "Fetch artifact",
      tool_name: "fetch",
      call_id: "call-retry-a1",
      attempt: 1,
    }),
  )
  controller.applyEvent(
    event(2, "task_event", {
      task_id: "task-cp2-retry",
      mode: "async",
      status: "failed",
      description: "Fetch artifact",
      tool_name: "fetch",
      call_id: "call-retry-a1",
      attempt: 1,
      error: "timeout after 5s",
    }),
  )
  controller.applyEvent(
    event(3, "task_event", {
      task_id: "task-cp2-retry",
      mode: "async",
      status: "running",
      description: "Fetch artifact",
      tool_name: "fetch",
      call_id: "call-retry-a2",
      attempt: 2,
    }),
  )
  controller.applyEvent(
    event(4, "task_event", {
      task_id: "task-cp2-retry",
      mode: "async",
      status: "completed",
      description: "Fetch artifact",
      tool_name: "fetch",
      call_id: "call-retry-a2",
      attempt: 2,
      output_excerpt: "artifact ready",
    }),
  )
  const summary = summarizeTaskboard(controller.getState())
  const stepLines = summary.rows[0]?.steps ?? []
  return {
    scenario: "cp2_failure_retry_completion",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      terminalCompleted: summary.rows[0]?.status === "completed",
      includesRetryAttemptGrammar: stepLines.some((line) => line.includes("(attempt 2)")),
      includesFailureSemantic: stepLines.some((line) => line.includes("[failed")),
    },
    summary,
  }
}

const runConcurrencyScenario = () => {
  const controller = createController()
  controller.runtimeFlags = {
    ...controller.runtimeFlags,
    subagentWorkGraphEnabled: true,
    subagentCoalesceMs: 0,
    subagentToastsEnabled: false,
    subagentMaxWorkItems: 12,
  }
  for (let i = 1; i <= 20; i += 1) {
    controller.applyEvent(
      event(i, "task_event", {
        task_id: `task-cp2-burst-${i}`,
        mode: "async",
        status: "running",
        description: `fanout-${i}`,
      }),
    )
  }
  const summary = summarizeTaskboard(controller.getState())
  return {
    scenario: "cp2_concurrency_20_tasks",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      boundedByMaxWorkItems: summary.itemCount === 12,
      newestTaskRetained: summary.rows.some((row) => row.id === "task-cp2-burst-20"),
      oldestTaskEvicted: !summary.rows.some((row) => row.id === "task-cp2-burst-1"),
    },
    summary,
  }
}

const main = (): void => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const captures = [
    runSyncProgressionScenario(),
    runAsyncWakeupScenario(),
    runRetryChainScenario(),
    runConcurrencyScenario(),
  ]
  for (const capture of captures) {
    const filePath = path.join(outputDir, `${capture.scenario}_capture_20260213.json`)
    writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
    console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
  }
}

main()
