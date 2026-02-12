import React from "react"
import { Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { render } from "ink"
import type { Instance as InkInstance } from "ink"
import { spawn } from "node:child_process"
import { createWriteStream, promises as fs } from "node:fs"
import path from "node:path"
import { ReplView } from "../../repl/components/ReplView.js"
import { ReplSessionController, type ReplState } from "./controller.js"
import { loadScript, runScript } from "./scriptRunner.js"
import type { ScriptRunResult } from "./scriptRunner.js"
import { renderStateToText } from "./renderText.js"
import { forgetSession } from "../../cache/sessionCache.js"
import type { ModelMenuItem, QueuedAttachment, SkillSelection } from "../../repl/types.js"
import { CliProviders } from "../../providers/cliProviders.js"
import type { PermissionDecision } from "../../repl/types.js"
import { resolveBreadboardPath, resolveBreadboardWorkspace } from "../../utils/paths.js"
import { resolveAsciiOnly, resolveColorMode } from "../../repl/designSystem.js"
import { resolveTuiConfig } from "../../tui_config/load.js"
import type { ResolvedTuiConfig } from "../../tui_config/types.js"

const DEFAULT_CONFIG = process.env.BREADBOARD_DEFAULT_CONFIG ?? "agent_configs/opencode_openrouter_grok4fast_cli_default.yaml"
const DEFAULT_SCRIPT_MAX_DURATION_MS = 180_000

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_CONFIG))
const workspaceOption = Options.text("workspace").pipe(Options.optional)
const modelOption = Options.text("model").pipe(Options.optional)
const remoteStreamOption = Options.boolean("remote-stream").pipe(Options.optional)
const permissionOption = Options.text("permission-mode").pipe(Options.optional)
const scriptOption = Options.text("script").pipe(Options.optional)
const scriptOutputOption = Options.text("script-output").pipe(Options.optional)
const scriptSnapshotsOption = Options.boolean("script-snapshots").pipe(Options.optional)
const scriptColorsOption = Options.boolean("script-color").pipe(Options.optional)
const scriptFinalOnlyOption = Options.boolean("script-final-only").pipe(Options.optional)
const scriptMaxDurationOption = Options.integer("script-max-duration-ms").pipe(Options.optional)
const tuiOption = Options.text("tui").pipe(Options.optional)
const tuiPresetOption = Options.text("tui-preset").pipe(Options.optional)
const tuiConfigOption = Options.text("tui-config").pipe(Options.optional)
const tuiConfigStrictOption = Options.boolean("tui-config-strict").pipe(Options.optional)

const getOptionValue = <T,>(value: Option.Option<T>): T | null => Option.getOrNull(value)

type StateDumpMode = "summary" | "full"
type TuiMode = "opentui" | "classic"

const parseStateDumpMode = (value: string | undefined): StateDumpMode => {
  const normalized = (value ?? "").trim().toLowerCase()
  return normalized === "full" ? "full" : "summary"
}

const normalizeTuiMode = (value?: string | null): TuiMode | null => {
  const normalized = (value ?? "").trim().toLowerCase()
  if (normalized === "opentui" || normalized === "classic") return normalized
  return null
}

const resolveTuiMode = (args: {
  scriptPath?: string | null
  cliValue?: string | null
}): TuiMode => {
  if (args.scriptPath) return "classic"
  const cliMode = normalizeTuiMode(args.cliValue)
  if (cliMode) return cliMode
  const envMode = normalizeTuiMode(process.env.BREADBOARD_TUI_MODE)
  if (envMode) return envMode
  return "opentui"
}

const runOpenTui = async (options: {
  configPath: string
  workspace?: string | null
  permissionMode?: string | null
}): Promise<void> => {
  const opentuiRoot = resolveBreadboardPath("opentui_slab")
  const args = ["run", "phaseB/controller.ts"]
  if (options.configPath) {
    args.push("--config", options.configPath)
  }
  if (options.workspace) {
    args.push("--workspace", options.workspace)
  }
  if (options.permissionMode) {
    args.push("--permission-mode", options.permissionMode)
  }
  const child = spawn("bun", args, {
    cwd: opentuiRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      BREADBOARD_ENGINE_PREFER_BUNDLE: "0",
    },
  })

  await new Promise<void>((resolve, reject) => {
    child.once("error", (err) => reject(err))
    child.once("exit", (code) => {
      if (code && code !== 0) {
        reject(new Error(`OpenTUI exited with code ${code}`))
        return
      }
      resolve()
    })
  })
}

const summarizeStateForDump = (state: ReplState) => {
  const lastConversation = state.conversation.length > 0 ? state.conversation[state.conversation.length - 1] : null
  const lastToolEvent = state.toolEvents.length > 0 ? state.toolEvents[state.toolEvents.length - 1] : null
  const modelMenu =
    state.modelMenu.status === "ready"
      ? {
          status: state.modelMenu.status,
          items: state.modelMenu.items.length,
          current: state.modelMenu.items.find((item) => item.isCurrent)?.value ?? null,
        }
      : state.modelMenu
  return {
    sessionId: state.sessionId,
    status: state.status,
    pendingResponse: state.pendingResponse,
    disconnected: state.disconnected,
    stats: state.stats,
    viewPrefs: state.viewPrefs,
    modelMenu,
    guardrailNotice: state.guardrailNotice ?? null,
    counts: {
      conversation: state.conversation.length,
      toolEvents: state.toolEvents.length,
      liveSlots: state.liveSlots.length,
      hints: state.hints.length,
    },
    lastConversation: lastConversation
      ? {
          id: lastConversation.id,
          speaker: lastConversation.speaker,
          phase: lastConversation.phase,
          preview: lastConversation.text.slice(0, 240),
        }
      : null,
    lastToolEvent: lastToolEvent
      ? {
          id: lastToolEvent.id,
          kind: lastToolEvent.kind,
          status: lastToolEvent.status,
          text: lastToolEvent.text.slice(0, 240),
        }
      : null,
  }
}

const startStateDump = async (
  controller: ReplSessionController,
): Promise<{ stop: () => Promise<void>; writeFinal: () => void }> => {
  const dumpPathRaw = process.env.BREADBOARD_STATE_DUMP_PATH?.trim()
  if (!dumpPathRaw) {
    return { stop: async () => undefined, writeFinal: () => undefined }
  }
  const dumpPath = path.isAbsolute(dumpPathRaw) ? dumpPathRaw : path.join(process.cwd(), dumpPathRaw)
  const mode = parseStateDumpMode(process.env.BREADBOARD_STATE_DUMP_MODE)
  const rateMs = Number(process.env.BREADBOARD_STATE_DUMP_RATE_MS ?? "100")
  const minIntervalMs = Number.isFinite(rateMs) && rateMs >= 0 ? rateMs : 100

  await fs.mkdir(path.dirname(dumpPath), { recursive: true })
  const stream = createWriteStream(dumpPath, { flags: "a" })
  let lastWrite = 0

  const writeEntry = (state: ReplState, reason: string) => {
    const now = Date.now()
    if (reason !== "final" && minIntervalMs > 0 && now - lastWrite < minIntervalMs) {
      return
    }
    lastWrite = now
    const payload =
      mode === "full"
        ? { timestamp: now, reason, state }
        : { timestamp: now, reason, state: summarizeStateForDump(state) }
    try {
      stream.write(`${JSON.stringify(payload)}\n`)
    } catch {
      // ignore
    }
  }

  const onChange = (state: ReplState) => writeEntry(state, "change")
  controller.on("change", onChange)
  onChange(controller.getState())

  return {
    writeFinal: () => writeEntry(controller.getState(), "final"),
    stop: async () => {
      controller.off("change", onChange)
      await new Promise<void>((resolve) => stream.end(() => resolve()))
    },
  }
}

const runInteractive = async (controller: ReplSessionController, tuiConfig: ResolvedTuiConfig) => {
  let state = controller.getState()
  let ink: InkInstance | null = null

  const handleSubmit = async (value: string, attachments?: ReadonlyArray<QueuedAttachment>) => {
    await controller.handleInput(value, attachments)
  }

  const handleModelSelect = async (item: ModelMenuItem) => {
    await controller.selectModel(item.value)
  }

  const handleModelMenuOpen = async () => {
    await controller.openModelMenu()
  }

  const handleModelMenuCancel = () => {
    controller.closeModelMenu()
  }

  const handleSkillsMenuOpen = async () => {
    await controller.openSkillsMenu()
  }

  const handleSkillsMenuCancel = () => {
    controller.closeSkillsMenu()
  }

  const handleSkillsApply = async (selection: SkillSelection) => {
    await controller.applySkillsSelection(selection)
  }

  const handleGuardrailToggle = () => {
    controller.toggleGuardrailNotice()
  }

  const handleGuardrailDismiss = () => {
    controller.dismissGuardrailNotice()
  }

  const handlePermissionDecision = async (decision: PermissionDecision) => {
    await controller.respondToPermission(decision)
  }

  const handleRewindClose = () => {
    controller.closeRewindMenu()
  }

  const handleRewindRestore = async (checkpointId: string, mode: "conversation" | "code" | "both") => {
    await controller.restoreCheckpoint(checkpointId, mode)
  }

  const handleListFiles = async (path?: string) => {
    return await controller.listFiles(path)
  }

  const handleReadFile = async (
    path: string,
    options?: { mode?: "cat" | "snippet"; headLines?: number; tailLines?: number; maxBytes?: number },
  ) => {
    return await controller.readFile(path, options)
  }

  const handleCtreeRequest = async (force?: boolean) => {
    await controller.requestCtreeTree(force)
  }

  const handleCtreeRefresh = async (options?: { stage?: string; includePreviews?: boolean; source?: string }) => {
    await controller.refreshCtreeTree(options)
  }

  const rerender = () => {
    if (!ink) return
    state = controller.getState()
    ink.rerender(
      <ReplView
        tuiConfig={tuiConfig}
        configPath={state.configPath ?? null}
        sessionId={state.sessionId}
        conversation={state.conversation}
        toolEvents={state.toolEvents}
        rawEvents={state.rawEvents}
        liveSlots={state.liveSlots}
        status={state.status}
        pendingResponse={state.pendingResponse}
        disconnected={state.disconnected}
        mode={state.mode}
        permissionMode={state.permissionMode}
        hints={state.hints}
        stats={state.stats}
        modelMenu={state.modelMenu}
        skillsMenu={state.skillsMenu}
        inspectMenu={state.inspectMenu}
        guardrailNotice={state.guardrailNotice}
        viewClearAt={state.viewClearAt ?? null}
        viewPrefs={state.viewPrefs}
        todos={state.todos}
        tasks={state.tasks}
        workGraph={state.workGraph}
        ctreeSnapshot={state.ctreeSnapshot ?? null}
        ctreeTree={state.ctreeTree ?? null}
        ctreeTreeStatus={state.ctreeTreeStatus}
        ctreeTreeError={state.ctreeTreeError ?? null}
        ctreeStage={state.ctreeStage}
        ctreeIncludePreviews={state.ctreeIncludePreviews}
        ctreeSource={state.ctreeSource}
        ctreeUpdatedAt={state.ctreeUpdatedAt ?? null}
        permissionRequest={state.permissionRequest}
        permissionError={state.permissionError}
        permissionQueueDepth={state.permissionQueueDepth}
        rewindMenu={state.rewindMenu}
        onSubmit={handleSubmit}
        onModelMenuOpen={handleModelMenuOpen}
        onModelSelect={handleModelSelect}
        onModelMenuCancel={handleModelMenuCancel}
        onSkillsMenuOpen={handleSkillsMenuOpen}
        onSkillsMenuCancel={handleSkillsMenuCancel}
        onSkillsApply={handleSkillsApply}
        onGuardrailToggle={handleGuardrailToggle}
        onGuardrailDismiss={handleGuardrailDismiss}
        onPermissionDecision={handlePermissionDecision}
        onRewindClose={handleRewindClose}
        onRewindRestore={handleRewindRestore}
        onListFiles={handleListFiles}
        onReadFile={handleReadFile}
        onCtreeRequest={handleCtreeRequest}
        onCtreeRefresh={handleCtreeRefresh}
      />,
    )
  }

  const unsubscribe = controller.onChange(() => rerender())

  const resizeHandler = () => rerender()
  if (process.stdout?.isTTY) {
    process.stdout.on("resize", resizeHandler)
  }

  ink = render(
      <ReplView
        tuiConfig={tuiConfig}
        configPath={state.configPath ?? null}
        sessionId={state.sessionId}
        conversation={state.conversation}
        toolEvents={state.toolEvents}
        rawEvents={state.rawEvents}
        liveSlots={state.liveSlots}
        status={state.status}
        pendingResponse={state.pendingResponse}
        disconnected={state.disconnected}
        mode={state.mode}
        permissionMode={state.permissionMode}
        hints={state.hints}
        stats={state.stats}
        modelMenu={state.modelMenu}
        skillsMenu={state.skillsMenu}
        inspectMenu={state.inspectMenu}
        guardrailNotice={state.guardrailNotice}
        viewClearAt={state.viewClearAt ?? null}
        viewPrefs={state.viewPrefs}
        todos={state.todos}
        tasks={state.tasks}
        workGraph={state.workGraph}
        ctreeSnapshot={state.ctreeSnapshot ?? null}
        ctreeTree={state.ctreeTree ?? null}
        ctreeTreeStatus={state.ctreeTreeStatus}
        ctreeTreeError={state.ctreeTreeError ?? null}
        ctreeStage={state.ctreeStage}
        ctreeIncludePreviews={state.ctreeIncludePreviews}
        ctreeSource={state.ctreeSource}
        ctreeUpdatedAt={state.ctreeUpdatedAt ?? null}
        permissionRequest={state.permissionRequest}
        permissionError={state.permissionError}
        permissionQueueDepth={state.permissionQueueDepth}
        rewindMenu={state.rewindMenu}
        onSubmit={handleSubmit}
        onModelMenuOpen={handleModelMenuOpen}
        onModelSelect={handleModelSelect}
        onModelMenuCancel={handleModelMenuCancel}
        onSkillsMenuOpen={handleSkillsMenuOpen}
        onSkillsMenuCancel={handleSkillsMenuCancel}
        onSkillsApply={handleSkillsApply}
        onGuardrailToggle={handleGuardrailToggle}
        onGuardrailDismiss={handleGuardrailDismiss}
        onPermissionDecision={handlePermissionDecision}
        onRewindClose={handleRewindClose}
        onRewindRestore={handleRewindRestore}
        onListFiles={handleListFiles}
        onReadFile={handleReadFile}
        onCtreeRequest={handleCtreeRequest}
        onCtreeRefresh={handleCtreeRefresh}
    />,
    { exitOnCtrlC: false },
  )

  const sigintHandler = async () => {
    await controller.stop()
  }
  process.once("SIGINT", sigintHandler)

  try {
    await controller.untilStopped()
  } finally {
    process.off("SIGINT", sigintHandler)
    if (process.stdout?.isTTY) {
      process.stdout.off("resize", resizeHandler)
    }
    unsubscribe()
    ink?.unmount()
  }
}

interface ScriptModeOptions {
  readonly outputPath?: string | null
  readonly snapshotEveryStep: boolean
  readonly useColors: boolean
  readonly finalOnly: boolean
  readonly maxDurationMs?: number | null
  readonly tuiConfig: ResolvedTuiConfig
}

const runScriptMode = async (controller: ReplSessionController, scriptPath: string, options: ScriptModeOptions) => {
  let sessionId: string | undefined
  let watchdogTimedOut = false
  let watchdogHandle: NodeJS.Timeout | null = null
  const stopTimeoutMs = 2_500
  const maxDuration =
    options.maxDurationMs != null && Number.isFinite(options.maxDurationMs) && options.maxDurationMs > 0
      ? options.maxDurationMs
      : DEFAULT_SCRIPT_MAX_DURATION_MS
  try {
    const initialState = controller.getState()
    sessionId = initialState.sessionId
    if (sessionId) {
      console.log(`Script session ${sessionId}`)
    }
    const definition = await loadScript(scriptPath)
    const scriptPromise = runScript(controller, definition, {
      snapshotAfterEachStep: options.snapshotEveryStep,
      finalOnly: options.finalOnly,
      renderOptions: {
        colors: options.useColors,
        colorMode: options.tuiConfig.display.colorMode ?? resolveColorMode(undefined, options.useColors),
        asciiOnly: options.tuiConfig.display.asciiOnly ?? resolveAsciiOnly(),
        includeStatus: options.finalOnly,
      },
    })
    const guardedPromise: Promise<ScriptRunResult> = maxDuration
      ? Promise.race<ScriptRunResult>([
          scriptPromise,
          new Promise<ScriptRunResult>((_, reject) => {
            watchdogHandle = setTimeout(() => {
              watchdogTimedOut = true
              reject(new Error(`Script runtime exceeded ${maxDuration}ms`))
            }, maxDuration)
          }),
        ])
      : scriptPromise

    const result = await guardedPromise
    const output = result.snapshots
      .map((snapshot) => [`# ${snapshot.label}`, snapshot.text].filter(Boolean).join("\n"))
      .join("\n\n")
    if (options.outputPath && options.outputPath.trim().length > 0) {
      const target = options.outputPath.trim()
      await fs.writeFile(target, output, "utf8")
      console.log(`Playback written to ${target}`)
    } else {
      console.log(output)
    }
  } finally {
    if (watchdogHandle) {
      clearTimeout(watchdogHandle)
    }
    const finalState = controller.getState()
    sessionId = finalState.sessionId || sessionId
    if (sessionId) {
      try {
        await CliProviders.sdk.api().deleteSession(sessionId)
        await forgetSession(sessionId)
      } catch (error) {
        console.error(`Failed to stop session ${sessionId}: ${(error as Error).message}`)
      }
    }
    let stopped = false
    await Promise.race([
      controller.stop().then(() => {
        stopped = true
      }),
      new Promise<void>((resolve) => setTimeout(resolve, stopTimeoutMs)),
    ])
    if (watchdogTimedOut) {
      console.error(
        `Script exceeded the ${maxDuration}ms limit; controller stopped and session ${sessionId ?? "(unknown)"} terminated.`,
      )
    }
    if (!stopped) {
      console.warn(`[repl script] Stream shutdown did not complete within ${stopTimeoutMs}ms.`)
    }
    if (process.env.BREADBOARD_SCRIPT_FORCE_EXIT !== "0") {
      process.exit(watchdogTimedOut ? 1 : 0)
    }
  }
}

const buildReplCommand = (name: string) =>
  Command.make(
    name,
    {
      config: configOption,
      workspace: workspaceOption,
      model: modelOption,
      remoteStream: remoteStreamOption,
      permissionMode: permissionOption,
      script: scriptOption,
      scriptOutput: scriptOutputOption,
      scriptSnapshots: scriptSnapshotsOption,
      scriptColor: scriptColorsOption,
      scriptFinalOnly: scriptFinalOnlyOption,
      scriptMaxDurationMs: scriptMaxDurationOption,
      tui: tuiOption,
      tuiPreset: tuiPresetOption,
      tuiConfigPath: tuiConfigOption,
      tuiConfigStrict: tuiConfigStrictOption,
    },
    ({
      config,
      workspace,
      model,
      remoteStream,
      permissionMode,
      script,
      scriptOutput,
      scriptSnapshots,
      scriptColor,
      scriptFinalOnly,
      scriptMaxDurationMs,
      tui,
      tuiPreset,
      tuiConfigPath,
      tuiConfigStrict,
    }) =>
      Effect.tryPromise(async () => {
        const scriptPath = getOptionValue(script)
        const tuiMode = resolveTuiMode({ scriptPath, cliValue: getOptionValue(tui) })
        const workspaceValue = getOptionValue(workspace)
        const modelValue = getOptionValue(model)
        const permissionValue = getOptionValue(permissionMode)
        const remotePreference = Option.match(remoteStream, {
          onNone: () => undefined,
          onSome: (value) => value,
        })
        const resolvedConfigPath = resolveBreadboardPath(config)
        const resolvedWorkspace = resolveBreadboardWorkspace(workspaceValue)
        const resolvedTuiConfig = await resolveTuiConfig({
          workspace: resolvedWorkspace,
          cliPreset: getOptionValue(tuiPreset),
          cliConfigPath: getOptionValue(tuiConfigPath),
          cliStrict: getOptionValue(tuiConfigStrict),
        })
        for (const warning of resolvedTuiConfig.meta.warnings) {
          console.warn(`[tui config] ${warning}`)
        }

        if (tuiMode === "opentui") {
          await runOpenTui({
            configPath: resolvedConfigPath,
            workspace: resolvedWorkspace,
            permissionMode: permissionValue ?? null,
          })
          return
        }

        if (!scriptPath && process.env.BREADBOARD_TUI_SUPPRESS_MAINTENANCE !== "1") {
          console.warn("[tui] Classic Ink TUI is in maintenance mode; use --tui opentui for the new slab UI.")
        }

        const controller = new ReplSessionController({
          configPath: resolvedConfigPath,
          workspace: resolvedWorkspace,
          model: modelValue ?? undefined,
          remotePreference,
          permissionMode: permissionValue ?? undefined,
        })

        await controller.start()
        const stateDump = await startStateDump(controller)

        try {
          if (scriptPath) {
            await runScriptMode(controller, scriptPath, {
              outputPath: getOptionValue(scriptOutput),
              snapshotEveryStep: getOptionValue(scriptSnapshots) ?? false,
              useColors: getOptionValue(scriptColor) ?? false,
              finalOnly: getOptionValue(scriptFinalOnly) ?? false,
              maxDurationMs: getOptionValue(scriptMaxDurationMs) ?? undefined,
              tuiConfig: resolvedTuiConfig,
            })
            return
          }

          await runInteractive(controller, resolvedTuiConfig)
          if (process.env.BREADBOARD_PRINT_FINAL_STATE === "1") {
            const finalState = controller.getState()
            console.log(
              renderStateToText(finalState, {
                colors: false,
                colorMode: resolvedTuiConfig.display.colorMode ?? resolveColorMode(undefined, false),
                asciiOnly: resolvedTuiConfig.display.asciiOnly ?? resolveAsciiOnly(),
              }),
            )
          }
          await controller.stop()
        } finally {
          stateDump.writeFinal()
          await stateDump.stop().catch(() => undefined)
        }
      }),
  )

export const createReplCommand = (name: string) => buildReplCommand(name)

export const replCommand = createReplCommand("repl")
