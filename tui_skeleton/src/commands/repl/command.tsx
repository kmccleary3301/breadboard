import React from "react"
import { Command, Options } from "@effect/cli"
import { Effect, Option } from "effect"
import { render } from "ink"
import type { Instance as InkInstance } from "ink"
import { createWriteStream, promises as fs } from "node:fs"
import path from "node:path"
import { ReplView } from "../../repl/components/ReplView.js"
import { ReplSessionController, type ReplState } from "./controller.js"
import { loadScript, runScript } from "./scriptRunner.js"
import type { ScriptRunResult } from "./scriptRunner.js"
import { renderStateToText } from "./renderText.js"
import { forgetSession } from "../../cache/sessionCache.js"
import type { ModelMenuItem, QueuedAttachment, SkillSelection, TaskEntry } from "../../repl/types.js"
import { CliProviders } from "../../providers/cliProviders.js"
import type { PermissionDecision } from "../../repl/types.js"
import { resolveBreadboardPath } from "../../utils/paths.js"
import { resolveAsciiOnly, resolveColorMode } from "../../repl/designSystem.js"
import { createScrollbackSafeInkStdout } from "../../repl/inkScrollbackStdout.js"
import type { ResolvedTuiConfig } from "../../tui_config/types.js"
import { DEFAULT_REPL_CONFIG_PATH, resolveReplLaunchContext } from "./launchContext.js"
import { runOpenTui } from "./frontendLaunch.js"
import { buildTranscript } from "../../repl/transcriptBuilder.js"
import { dumpTranscriptCellRecords } from "../../repl/transcriptModel.js"
import { resolveSurfaceRenderPolicy, type SurfacePolicyKind } from "../../repl/renderPolicy.js"

// Product-facing entry points default to the Codex E4 lane. Users and harnesses
// may still override this via BREADBOARD_DEFAULT_CONFIG or --config.
const DEFAULT_SCRIPT_MAX_DURATION_MS = 180_000

const configOption = Options.text("config").pipe(Options.withDefault(DEFAULT_REPL_CONFIG_PATH))
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
const engineModeOption = Options.text("engine-mode").pipe(Options.optional)

const getOptionValue = <T,>(value: Option.Option<T>): T | null => Option.getOrNull(value)

type StateDumpMode = "summary" | "full"
const parseStateDumpMode = (value: string | undefined): StateDumpMode => {
  const normalized = (value ?? "").trim().toLowerCase()
  return normalized === "full" ? "full" : "summary"
}

const summarizeStateForDump = (state: ReplState) => {
  const lastConversation = state.conversation.length > 0 ? state.conversation[state.conversation.length - 1] : null
  const lastToolEvent = state.toolEvents.length > 0 ? state.toolEvents[state.toolEvents.length - 1] : null
  const transcript = buildTranscript({
    conversation: state.conversation,
    toolEvents: state.toolEvents,
    rawEvents: state.rawEvents,
  })
  const transcriptCells = dumpTranscriptCellRecords([...transcript.committed, ...transcript.tail])
  const modelMenu =
    state.modelMenu.status === "ready"
      ? {
          status: state.modelMenu.status,
          items: state.modelMenu.items.length,
          current: state.modelMenu.items.find((item) => item.isCurrent)?.value ?? null,
        }
      : state.modelMenu
  const surfacePolicies = buildSurfacePoliciesForDump(state)
  return {
    sessionId: state.sessionId,
    lifecycle: state.lifecycle ?? null,
    lifecycleRestartCount: state.lifecycleRestartCount ?? 0,
    status: state.status,
    pendingResponse: state.pendingResponse,
    mainFollowTail: state.mainFollowTail,
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
      transcriptCells: transcriptCells.length,
      surfacePolicies: surfacePolicies.length,
    },
    surfacePolicies,
    transcriptCells,
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

const surfacePolicyRecord = (
  id: string,
  kind: SurfacePolicyKind,
  visible: boolean,
  status: string,
) => {
  const renderPolicy = resolveSurfaceRenderPolicy(kind)
  return {
    id,
    visible,
    status,
    renderPolicy,
    ownershipClass: renderPolicy.ownershipClass,
    stabilityState: renderPolicy.stabilityState,
    contentSafetyClass: renderPolicy.contentSafetyClass,
    widthPolicy: renderPolicy.widthPolicy,
    heightPolicy: renderPolicy.heightPolicy,
    truncationPolicy: renderPolicy.truncationPolicy,
    detailPolicy: renderPolicy.detailPolicy,
    priority: renderPolicy.priority,
  }
}

const menuVisible = (state: { readonly status?: string } | null | undefined): boolean =>
  Boolean(state && state.status && state.status !== "hidden")

const buildSurfacePoliciesForDump = (state: ReplState) => {
  const surfaces = [
    surfacePolicyRecord("composer:input", "composer", true, "active"),
    surfacePolicyRecord("footer:status", "footer", true, state.disconnected ? "disconnected" : state.pendingResponse ? "pending" : "ready"),
    surfacePolicyRecord("overlay:modal-stack", "overlay", false, "hidden"),
  ]

  if (menuVisible(state.modelMenu)) surfaces.push(surfacePolicyRecord("overlay:model-menu", "overlay", true, state.modelMenu.status))
  if (menuVisible(state.skillsMenu)) surfaces.push(surfacePolicyRecord("overlay:skills-menu", "overlay", true, state.skillsMenu.status))
  if (menuVisible(state.inspectMenu)) surfaces.push(surfacePolicyRecord("overlay:inspect-menu", "overlay", true, state.inspectMenu.status))
  if (menuVisible(state.rewindMenu)) surfaces.push(surfacePolicyRecord("overlay:rewind-menu", "overlay", true, state.rewindMenu.status))
  if (state.permissionRequest) surfaces.push(surfacePolicyRecord("overlay:permission-request", "overlay", true, "ready"))

  return surfaces
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
  let lastCriticalSignature = ""

  const writeEntry = (state: ReplState, reason: string) => {
    const now = Date.now()
    const lastConversation = state.conversation.length > 0 ? state.conversation[state.conversation.length - 1] : null
    const lastToolEvent = state.toolEvents.length > 0 ? state.toolEvents[state.toolEvents.length - 1] : null
    const criticalSignature = [
      state.pendingResponse ? "pending" : "idle",
      state.disconnected ? "disconnected" : "connected",
      state.mainFollowTail ? "tail" : "paused",
      state.status,
      state.stats.eventCount,
      state.conversation.length,
      lastConversation?.speaker ?? "",
      lastConversation?.phase ?? "",
      lastToolEvent?.kind ?? "",
      lastToolEvent?.status ?? "",
      state.lifecycle?.mode ?? "",
      state.lifecycle?.pid ?? "",
      state.lifecycleRestartCount ?? 0,
      state.modelMenu.status,
      state.skillsMenu.status,
      state.inspectMenu.status,
      state.rewindMenu.status,
      state.permissionRequest?.requestId ?? "",
    ].join("|")
    const criticalChanged = criticalSignature !== lastCriticalSignature
    if (reason !== "final" && !criticalChanged && minIntervalMs > 0 && now - lastWrite < minIntervalMs) {
      return
    }
    lastWrite = now
    lastCriticalSignature = criticalSignature
    const payload =
      mode === "full"
        ? {
            timestamp: now,
            reason,
            state,
            transcriptCells: summarizeStateForDump(state).transcriptCells,
            surfacePolicies: summarizeStateForDump(state).surfacePolicies,
          }
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

const runInteractive = async (
  controller: ReplSessionController,
  tuiConfig: ResolvedTuiConfig,
  liveShellOwnershipMode: import("../../config/frontendMode.js").LiveShellOwnershipMode,
  liveShellRendererHost: import("../../config/frontendMode.js").LiveShellRendererHost,
  liveShellSceneStrategy: import("../../config/frontendMode.js").LiveShellSceneStrategy,
) => {
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

  const handleTaskAction = async (
    action: "cancel" | "retry" | "pause_resume" | "merge",
    task: TaskEntry,
  ): Promise<boolean> => {
    const taskId = String(task.id ?? "").trim()
    if (!taskId) return false
    const label = action === "pause_resume" ? "pause/resume" : action
    const payload: Record<string, unknown> = {
      action,
      task_id: taskId,
    }
    if (task.sessionId) payload.task_session_id = task.sessionId
    if (task.subagentType) payload.subagent_type = task.subagentType
    if (task.status) payload.status = task.status
    return await controller.runSessionCommand("task_action", payload, `Task ${label} requested for ${taskId}.`)
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

  const handleListRecentSessions = async () => {
    return await controller.listRecentSessions()
  }

  const handleAttachSession = async (sessionId: string) => {
    return await controller.attachExistingSession(sessionId)
  }

  const handleReadFile = async (
    path: string,
    options?: { mode?: "cat" | "snippet"; headLines?: number; tailLines?: number; maxBytes?: number },
  ) => {
    return await controller.readFile(path, options)
  }

  const handleReadWorkingTreeDiff = async () => {
    return await controller.readWorkingTreeDiff()
  }

  const handleExportWorkingTreeDiffPatch = async (targetPath?: string | null) => {
    return await controller.exportWorkingTreeDiffPatch(targetPath)
  }

  const handleCopyWorkingTreeDiffPatch = async () => {
    return await controller.copyWorkingTreeDiffPatch()
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
        liveShellOwnershipMode={liveShellOwnershipMode}
        liveShellRendererHost={liveShellRendererHost}
        liveShellSceneStrategy={liveShellSceneStrategy}
        configPath={state.configPath ?? null}
        sessionId={state.sessionId}
        conversation={state.conversation}
        toolEvents={state.toolEvents}
        rawEvents={state.rawEvents}
        liveSlots={state.liveSlots}
        status={state.status}
        pendingResponse={state.pendingResponse}
        mainFollowTail={state.mainFollowTail}
        disconnected={state.disconnected}
        mode={state.mode}
        permissionMode={state.permissionMode}
        hints={state.hints}
        stats={state.stats}
        modelMenu={state.modelMenu}
        skillsMenu={state.skillsMenu}
        inspectMenu={state.inspectMenu}
        guardrailNotice={state.guardrailNotice}
        activity={state.activity}
        runtimeFlags={state.runtimeFlags}
        thinkingArtifact={state.thinkingArtifact}
        thinkingPreview={state.thinkingPreview}
        viewClearAt={state.viewClearAt ?? null}
        viewPrefs={state.viewPrefs}
        todoScopeKey={state.todoScopeKey}
        todoScopeLabel={state.todoScopeLabel}
        todoScopeStale={state.todoScopeStale}
        todoScopeOrder={state.todoScopeOrder}
        todoStore={state.todoStore}
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
        onTaskAction={handleTaskAction}
        onRewindClose={handleRewindClose}
        onRewindRestore={handleRewindRestore}
        onListFiles={handleListFiles}
        onListRecentSessions={handleListRecentSessions}
        onAttachSession={handleAttachSession}
        onReadFile={handleReadFile}
        onReadWorkingTreeDiff={handleReadWorkingTreeDiff}
        onExportWorkingTreeDiffPatch={handleExportWorkingTreeDiffPatch}
        onCopyWorkingTreeDiffPatch={handleCopyWorkingTreeDiffPatch}
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
        liveShellOwnershipMode={liveShellOwnershipMode}
        liveShellRendererHost={liveShellRendererHost}
        configPath={state.configPath ?? null}
        sessionId={state.sessionId}
        conversation={state.conversation}
        toolEvents={state.toolEvents}
        rawEvents={state.rawEvents}
        liveSlots={state.liveSlots}
        status={state.status}
        pendingResponse={state.pendingResponse}
        mainFollowTail={state.mainFollowTail}
        disconnected={state.disconnected}
        mode={state.mode}
        permissionMode={state.permissionMode}
        hints={state.hints}
        stats={state.stats}
        modelMenu={state.modelMenu}
        skillsMenu={state.skillsMenu}
        inspectMenu={state.inspectMenu}
        guardrailNotice={state.guardrailNotice}
        activity={state.activity}
        runtimeFlags={state.runtimeFlags}
        thinkingArtifact={state.thinkingArtifact}
        thinkingPreview={state.thinkingPreview}
        viewClearAt={state.viewClearAt ?? null}
        viewPrefs={state.viewPrefs}
        todoScopeKey={state.todoScopeKey}
        todoScopeLabel={state.todoScopeLabel}
        todoScopeStale={state.todoScopeStale}
        todoScopeOrder={state.todoScopeOrder}
        todoStore={state.todoStore}
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
        onTaskAction={handleTaskAction}
        onRewindClose={handleRewindClose}
        onRewindRestore={handleRewindRestore}
        onListFiles={handleListFiles}
        onListRecentSessions={handleListRecentSessions}
        onAttachSession={handleAttachSession}
        onReadFile={handleReadFile}
        onReadWorkingTreeDiff={handleReadWorkingTreeDiff}
        onExportWorkingTreeDiffPatch={handleExportWorkingTreeDiffPatch}
        onCopyWorkingTreeDiffPatch={handleCopyWorkingTreeDiffPatch}
        onCtreeRequest={handleCtreeRequest}
        onCtreeRefresh={handleCtreeRefresh}
    />,
    {
      exitOnCtrlC: false,
      stdout: createScrollbackSafeInkStdout(process.stdout, liveShellOwnershipMode === "inline-scrollback"),
    },
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
      engineMode: engineModeOption,
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
      engineMode: _engineMode,
    }) =>
      Effect.tryPromise(async () => {
        const launch = await resolveReplLaunchContext({
          config,
          workspace: getOptionValue(workspace),
          model: getOptionValue(model),
          remoteStream,
          permissionMode: getOptionValue(permissionMode),
          scriptPath: getOptionValue(script),
          tui: getOptionValue(tui),
          tuiPreset: getOptionValue(tuiPreset),
          tuiConfigPath: getOptionValue(tuiConfigPath),
          tuiConfigStrict: getOptionValue(tuiConfigStrict),
        })
        const {
          frontend: tuiMode,
          liveShellOwnershipMode,
          liveShellRendererHost,
          liveShellSceneStrategy,
          scriptPath,
          resolvedConfigPath,
          resolvedWorkspace,
          modelValue,
          permissionValue,
          remotePreference,
          resolvedTuiConfig,
        } = launch
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

        const controller = new ReplSessionController({
          configPath: resolvedConfigPath,
          workspace: resolvedWorkspace,
          model: modelValue ?? undefined,
          remotePreference,
          permissionMode: permissionValue ?? undefined,
          todoAutoFollowScope: resolvedTuiConfig.composer.todoAutoFollowScope,
          todoAutoFollowHysteresisMs: resolvedTuiConfig.composer.todoAutoFollowHysteresisMs,
          todoAutoFollowManualOverrideMs: resolvedTuiConfig.composer.todoAutoFollowManualOverrideMs,
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

          await runInteractive(
            controller,
            resolvedTuiConfig,
            liveShellOwnershipMode,
            liveShellRendererHost,
            liveShellSceneStrategy,
          )
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
