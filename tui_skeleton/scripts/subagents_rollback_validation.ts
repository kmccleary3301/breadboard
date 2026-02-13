import { writeFileSync } from "node:fs"
import path from "node:path"
import { resolveRuntimeBehaviorFlags } from "../src/commands/repl/controllerActivityRuntime.js"
import { resolveSubagentUiPolicy } from "../src/commands/repl/subagentUiPolicy.js"

type EnvMap = Record<string, string>

interface Args {
  strict: boolean
  out: string | null
  markdownOut: string | null
}

interface LevelResult {
  level: string
  description: string
  env: EnvMap
  checks: Record<string, boolean>
  flags: Record<string, unknown>
  policy: Record<string, unknown>
  tui: Record<string, unknown>
  ok: boolean
}

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let strict = false
  let out: string | null = null
  let markdownOut: string | null = null
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--strict") {
      strict = true
      continue
    }
    if (arg === "--out") {
      out = argv[index + 1] ?? null
      index += 1
      continue
    }
    if (arg === "--markdown-out") {
      markdownOut = argv[index + 1] ?? null
      index += 1
      continue
    }
  }
  return { strict, out, markdownOut }
}

const withEnv = async <T>(env: EnvMap, callback: () => Promise<T>): Promise<T> => {
  const previous = new Map<string, string | undefined>()
  for (const [key, value] of Object.entries(env)) {
    previous.set(key, process.env[key])
    process.env[key] = value
  }
  try {
    return await callback()
  } finally {
    for (const [key, value] of previous.entries()) {
      if (value == null) delete process.env[key]
      else process.env[key] = value
    }
  }
}

const buildLevels = (): Array<{ level: string; description: string; env: EnvMap }> => [
  {
    level: "L0",
    description: "Full subagents surfaces enabled",
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
    },
  },
  {
    level: "L1",
    description: "Focus disabled, strip/toasts/taskboard kept",
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "0",
    },
  },
  {
    level: "L2",
    description: "Taskboard/focus disabled, strip/toasts retained",
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "0",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "0",
    },
  },
  {
    level: "L3",
    description: "V2 disabled, legacy tool-rail route restored",
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "0",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "0",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "0",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "0",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "0",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "0",
    },
  },
]

const evaluateLevel = async (level: string, description: string, env: EnvMap): Promise<LevelResult> =>
  withEnv(env, async () => {
    const { resolveTuiConfig } = await import("../src/tui_config/load.js")
    const flags = resolveRuntimeBehaviorFlags(process.env)
    const policy = resolveSubagentUiPolicy(flags, process.env)
    const tuiConfig = await resolveTuiConfig({
      workspace: ".",
      cliStrict: true,
      colorAllowed: true,
    })

    const checks: Record<string, boolean> = {
      v2FlagMatchesExpectation: Boolean(flags.subagentWorkGraphEnabled) === (env.BREADBOARD_SUBAGENTS_V2_ENABLED === "1"),
      focusFlagMatchesExpectation: Boolean(flags.subagentFocusEnabled) === (env.BREADBOARD_SUBAGENTS_FOCUS_ENABLED === "1"),
      taskboardFlagMatchesExpectation:
        Boolean(flags.subagentTaskboardEnabled) === (env.BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED === "1"),
      tuiFocusMatchesExpectation: Boolean(tuiConfig.subagents.focusEnabled) === (env.BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED === "1"),
      tuiTaskboardMatchesExpectation:
        Boolean(tuiConfig.subagents.taskboardEnabled) === (env.BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED === "1"),
      toolRailRoutingConsistent: Boolean(policy.routeTaskEventsToToolRail) === !Boolean(flags.subagentWorkGraphEnabled),
    }

    return {
      level,
      description,
      env,
      checks,
      flags: {
        subagentWorkGraphEnabled: flags.subagentWorkGraphEnabled,
        subagentStripEnabled: flags.subagentStripEnabled,
        subagentToastsEnabled: flags.subagentToastsEnabled,
        subagentTaskboardEnabled: flags.subagentTaskboardEnabled,
        subagentFocusEnabled: flags.subagentFocusEnabled,
      },
      policy: {
        v2Enabled: policy.v2Enabled,
        stripEnabled: policy.stripEnabled,
        toastsEnabled: policy.toastsEnabled,
        taskboardEnabled: policy.taskboardEnabled,
        focusEnabled: policy.focusEnabled,
        routeTaskEventsToToolRail: policy.routeTaskEventsToToolRail,
        routeTaskEventsToLiveSlots: policy.routeTaskEventsToLiveSlots,
      },
      tui: {
        taskboardEnabled: tuiConfig.subagents.taskboardEnabled,
        focusEnabled: tuiConfig.subagents.focusEnabled,
      },
      ok: Object.values(checks).every(Boolean),
    }
  })

const toMarkdown = (summary: any): string => {
  const lines: string[] = []
  lines.push("# Subagents Rollback Validation")
  lines.push("")
  lines.push(`- generatedAt: \`${summary.generatedAt}\``)
  lines.push(`- strict: \`${String(summary.strict)}\``)
  lines.push(`- totalLevels: \`${summary.totalLevels}\``)
  lines.push(`- failedLevels: \`${summary.failedLevels}\``)
  lines.push("")
  lines.push("| Level | Result | Description |")
  lines.push("| --- | --- | --- |")
  for (const level of summary.levels as LevelResult[]) {
    lines.push(`| ${level.level} | ${level.ok ? "ok" : "fail"} | ${level.description} |`)
  }
  lines.push("")
  return `${lines.join("\n")}\n`
}

const main = async (): Promise<void> => {
  const args = parseArgs()
  const results: LevelResult[] = []
  for (const level of buildLevels()) {
    results.push(await evaluateLevel(level.level, level.description, level.env))
  }
  const failedLevels = results.filter((entry) => !entry.ok)
  const summary = {
    ok: failedLevels.length === 0,
    strict: args.strict,
    generatedAt: new Date().toISOString(),
    totalLevels: results.length,
    failedLevels: failedLevels.length,
    levels: results,
  }
  const output = JSON.stringify(summary, null, 2)
  console.log(output)
  if (args.out) writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  if (args.markdownOut) writeFileSync(path.resolve(args.markdownOut), toMarkdown(summary), "utf8")
  if (args.strict && !summary.ok) process.exit(1)
}

void main()
