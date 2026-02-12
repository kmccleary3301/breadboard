import { EventEmitter } from "node:events"
import { promises as fs } from "node:fs"
import path from "node:path"
import type { Block } from "@stream-mdx/core/types"
import type { MarkdownStreamer } from "../../markdown/streamer.js"
import type {
  ActivitySnapshot,
  RuntimeTelemetry,
  RuntimeBehaviorFlags,
  ThinkingArtifact,
  ProviderCapabilitiesSnapshot,
  ModelMenuItem,
  ModelMenuState,
  ConversationEntry,
  StreamStats,
  CompletionState,
  LiveSlotEntry,
  LiveSlotStatus,
  GuardrailNotice,
  QueuedAttachment,
  TranscriptPreferences,
  ToolLogEntry,
  ToolLogKind,
  TodoItem,
  TaskEntry,
  SkillCatalog,
  SkillSelection,
  SkillCatalogSources,
  SkillsMenuState,
  InspectMenuState,
  CTreeSnapshot,
  PermissionRequest,
  PermissionDecision,
  PermissionRuleScope,
  RewindMenuState,
  CheckpointSummary,
} from "../../repl/types.js"
import { ApiError } from "../../api/client.js"
import type { ReadSessionFileOptions } from "../../api/client.js"
import type {
  SessionEvent,
  SessionFileInfo,
  SessionFileContent,
  CTreeTreeResponse,
  CTreeTreeStage,
  CTreeTreeSource,
} from "../../api/types.js"
import { createEmptyCTreeModel, type CTreeModel } from "../../repl/ctrees/reducer.js"
import { DEFAULT_MODEL_ID } from "../../config/appConfig.js"
import { getModelCatalog } from "../../providers/modelCatalog.js"
import { CliProviders } from "../../providers/cliProviders.js"
import {
  applySkillsSelection,
  closeInspectMenu,
  closeModelMenu,
  closeSkillsMenu,
  dispatchSlashCommand,
  listFiles,
  loadModelMenuItems,
  openInspectMenu,
  openModelMenu,
  openSkillsMenu,
  readFile,
  refreshInspectMenu,
  runSessionCommand,
  selectModel,
  waitFor,
  waitForCompletion,
} from "./controllerUserMethods.js"
import { DEFAULT_RICH_MARKDOWN, normalizeModeValue, normalizePermissionMode } from "./controllerUtils.js"
import * as stateMethods from "./controllerStateMethods.js"
import * as renderState from "./controllerStateRender.js"
import * as eventMethods from "./controllerEventMethods.js"
import * as ctreeMethods from "./controllerEventCtree.js"
import {
  bumpTelemetry,
  createActivitySnapshot,
  createRuntimeTelemetry,
  reduceActivityTransition,
  resolveRuntimeBehaviorFlags,
  type ActivityTransitionInput,
} from "./controllerActivityRuntime.js"
import type { ActivityTransitionTrace } from "./controllerTransitionTimeline.js"
import { resolveProviderCapabilities } from "./providerCapabilityResolution.js"

export interface ReplControllerOptions {
  readonly configPath: string
  readonly workspace?: string | null
  readonly model?: string | null
  readonly remotePreference?: boolean | null
  readonly permissionMode?: string | null
}

export interface CompletionView {
  readonly completed: boolean
  readonly status: string
  readonly toolLine: string
  readonly hint?: string
  readonly conversationLine?: string
  readonly warningSlot?: { readonly text: string; readonly color?: string }
}

export interface ReplState {
  readonly configPath?: string | null
  readonly sessionId: string
  readonly status: string
  readonly pendingResponse: boolean
  readonly mode?: string | null
  readonly permissionMode?: string | null
  readonly conversation: ConversationEntry[]
  readonly toolEvents: ToolLogEntry[]
  readonly liveSlots: LiveSlotEntry[]
  readonly rawEvents: ToolLogEntry[]
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly skillsMenu: SkillsMenuState
  readonly inspectMenu: InspectMenuState
  readonly completionReached: boolean
  readonly completionSeen: boolean
  readonly lastCompletion?: CompletionState | null
  readonly activity?: ActivitySnapshot
  readonly runtimeTelemetry?: RuntimeTelemetry
  readonly runtimeFlags?: RuntimeBehaviorFlags
  readonly thinkingArtifact?: ThinkingArtifact | null
  readonly providerCapabilities?: ProviderCapabilitiesSnapshot
  readonly activityTransitionTrace?: ReadonlyArray<ActivityTransitionTrace>
  readonly disconnected: boolean
  readonly guardrailNotice?: GuardrailNotice | null
  readonly viewClearAt?: number | null
  readonly viewPrefs: TranscriptPreferences
  readonly permissionRequest?: PermissionRequest | null
  readonly permissionError?: string | null
  readonly permissionQueueDepth?: number
  readonly rewindMenu: RewindMenuState
  readonly todos: TodoItem[]
  readonly tasks: TaskEntry[]
  readonly ctreeSnapshot?: CTreeSnapshot | null
  readonly ctreeTree?: CTreeTreeResponse | null
  readonly ctreeModel?: CTreeModel | null
  readonly ctreeTreeStatus: "idle" | "loading" | "error"
  readonly ctreeTreeError?: string | null
  readonly ctreeStage: CTreeTreeStage | string
  readonly ctreeIncludePreviews: boolean
  readonly ctreeSource: CTreeTreeSource | string
  readonly ctreeUpdatedAt?: number | null
}

type StateListener = (state: ReplState) => void

type SlashHandler = (args: string[]) => Promise<void>

interface SubmissionPayload {
  readonly content: string
  readonly attachments?: ReadonlyArray<string>
}

export class ReplSessionController extends EventEmitter {
  private readonly config: ReplControllerOptions
  private readonly conversation: ConversationEntry[] = []
  private readonly toolEvents: ToolLogEntry[] = []
  private readonly liveSlots = new Map<string, LiveSlotEntry>()
  private readonly liveSlotTimers = new Map<string, NodeJS.Timeout>()
  private guardrailNotice: GuardrailNotice | null = null
  private readonly toolSlotsByCallId = new Map<string, string>()
  private readonly toolSlotFallback: string[] = []
  private readonly toolLogEntryByCallId = new Map<string, string>()
  private readonly toolCallArgsById = new Map<string, string>()
  private readonly toolExecOutputByCallId = new Map<string, { stdout: string; stderr: string }>()
  private readonly toolExecSlotsById = new Map<string, string>()
  private readonly toolExecMetaById = new Map<string, { toolName?: string; command?: string }>()
  private readonly hints: string[] = []
  private readonly rawEvents: ToolLogEntry[] = []
  private readonly seenEventIds = new Set<string>()
  private readonly seenEventIdQueue: string[] = []
  private conversationSequence = 0
  private streamingEntryId: string | null = null
  private viewPrefs: TranscriptPreferences = {
    collapseMode: "none",
    virtualization: "auto",
    richMarkdown: DEFAULT_RICH_MARKDOWN,
    toolRail: true,
    toolInline: false,
    rawStream: false,
    showReasoning: false,
    diffLineNumbers: ["1", "true", "yes", "on"].includes(
      (process.env.BREADBOARD_DIFF_LINE_NUMBERS ?? "").toLowerCase(),
    ),
  }
  private submissionHistory: SubmissionPayload[] = []
  private readonly stats: StreamStats = {
    eventCount: 0,
    toolCount: 0,
    lastTurn: null,
    remote: false,
    model: DEFAULT_MODEL_ID,
  }
  private emitScheduled = false
  private status = "Starting session…"
  private statusUpdatedAt = Date.now()
  private pendingResponse = false
  private pendingStartedAt: number | null = null
  private modelMenu: ModelMenuState = { status: "hidden" }
  private skillsMenu: SkillsMenuState = { status: "hidden" }
  private inspectMenu: InspectMenuState = { status: "hidden" }
  private skillsCatalog: SkillCatalog | null = null
  private skillsSelection: SkillSelection | null = null
  private skillsSources: SkillCatalogSources | null = null
  private ctreeSnapshot: CTreeSnapshot | null = null
  private ctreeTree: CTreeTreeResponse | null = null
  private ctreeModel: CTreeModel = createEmptyCTreeModel()
  private ctreeTreeStatus: "idle" | "loading" | "error" = "idle"
  private ctreeTreeError: string | null = null
  private ctreeStage: CTreeTreeStage | string = "FROZEN"
  private ctreeIncludePreviews = false
  private ctreeSource: CTreeTreeSource | string = "auto"
  private ctreeUpdatedAt: number | null = null
  private ctreeRefreshTimer: NodeJS.Timeout | null = null
  private ctreeRefreshPending = false
  private ctreeRefreshInFlight = false
  private ctreeTreeRequested = false
  private completionReached = false
  private completionSeen = false
  private lastCompletion: CompletionState | null = null
  private disconnected = false
  private sessionId = ""
  private abortController = new AbortController()
  private consecutiveFailures = 0
  private awaitingRestart = false
  private abortRequested = false
  private streamTask: Promise<void> | null = null
  private pendingEvents: SessionEvent[] = []
  private eventsScheduled = false
  private readonly providers = CliProviders
  private readonly markdownStreams = new Map<string, { streamer: MarkdownStreamer; lastText: string }>()
  private readonly markdownStableState = new Map<
    string,
    {
      fullText: string
      emittedLen: number
      stableBoundaryLen: number
      state: { inFence: boolean; fenceMarker: "```" | "~~~" | null; inList: boolean; inTable: boolean }
    }
  >()
  private readonly markdownPendingDeltas = new Map<string, { buffer: string; timer: NodeJS.Timeout | null }>()
  private pendingUserEcho: string | null = null
  private markdownGloballyDisabled = false
  private viewClearAt: number | null = null
  private permissionActive: PermissionRequest | null = null
  private permissionError: string | null = null
  private permissionQueue: PermissionRequest[] = []
  private rewindMenu: RewindMenuState = { status: "hidden" }
  private todos: TodoItem[] = []
  private tasks: TaskEntry[] = []
  private lastEventId: string | null = null
  private eventClock = 0
  private currentEventSeq: number | null = null
  private hasStreamedOnce = false
  private stopRequestedAt: number | null = null
  private stopTimer: NodeJS.Timeout | null = null
  private readonly taskMap = new Map<string, TaskEntry>()
  private mode: string | null = null
  private permissionMode: string | null = null
  private runtimeFlags: RuntimeBehaviorFlags = resolveRuntimeBehaviorFlags(process.env)
  private runtimeTelemetry: RuntimeTelemetry = createRuntimeTelemetry()
  private activity: ActivitySnapshot = createActivitySnapshot("idle")
  private activityTransitionTrace: ActivityTransitionTrace[] = []
  private lastLifecycleToastAt = 0
  private lastLifecycleToastMessage: string | null = null
  private thinkingArtifact: ThinkingArtifact | null = null
  private thinkingInlineEntryId: string | null = null
  private lastThinkingPeekAt = 0
  private providerCapabilities: ProviderCapabilitiesSnapshot = {
    provider: "unknown",
    model: null,
    reasoningEvents: true,
    thoughtSummaryEvents: true,
    contextUsage: true,
    activitySurface: true,
    rawThinkingPeek: false,
    inlineThinkingBlock: false,
    warnings: [],
  }

  constructor(options: ReplControllerOptions) {
    super()
    this.config = options
  }

  private api() {
    return this.providers.sdk.api()
  }

  getState(): ReplState {
    const liveSlotList = Array.from(this.liveSlots.values()).sort((a, b) => a.updatedAt - b.updatedAt)
    return {
      configPath: this.config.configPath,
      sessionId: this.sessionId,
      status: this.status,
      pendingResponse: this.pendingResponse,
      mode: this.mode,
      permissionMode: this.permissionMode,
      conversation: [...this.conversation],
      toolEvents: [...this.toolEvents],
      liveSlots: liveSlotList,
      rawEvents: [...this.rawEvents],
      hints: [...this.hints],
      stats: { ...this.stats },
      modelMenu: this.modelMenu,
      skillsMenu: this.skillsMenu,
      inspectMenu: this.inspectMenu,
      completionReached: this.completionReached,
      completionSeen: this.completionSeen,
      lastCompletion: this.lastCompletion,
      activity: this.activity,
      runtimeTelemetry: this.runtimeTelemetry,
      runtimeFlags: this.runtimeFlags,
      thinkingArtifact: this.thinkingArtifact,
      providerCapabilities: this.providerCapabilities,
      activityTransitionTrace: [...this.activityTransitionTrace],
      disconnected: this.disconnected,
      guardrailNotice: this.guardrailNotice,
      viewClearAt: this.viewClearAt,
      viewPrefs: this.viewPrefs,
      permissionRequest: this.permissionActive,
      permissionError: this.permissionError,
      permissionQueueDepth: this.permissionQueue.length,
      rewindMenu: this.rewindMenu,
      todos: [...this.todos],
      tasks: [...this.tasks],
      ctreeSnapshot: this.ctreeSnapshot,
      ctreeTree: this.ctreeTree,
      ctreeModel: this.ctreeModel,
      ctreeTreeStatus: this.ctreeTreeStatus,
      ctreeTreeError: this.ctreeTreeError,
      ctreeStage: this.ctreeStage,
      ctreeIncludePreviews: this.ctreeIncludePreviews,
      ctreeSource: this.ctreeSource,
      ctreeUpdatedAt: this.ctreeUpdatedAt,
    }
  }

  onChange(listener: StateListener): () => void {
    this.on("change", listener)
    listener(this.getState())
    return () => this.off("change", listener)
  }

  async start(): Promise<void> {
    const appConfig = this.providers.args.config
    this.runtimeFlags = resolveRuntimeBehaviorFlags(process.env)
    this.resolveProviderCapabilitiesSnapshot(this.config.model?.trim() ?? null)
    this.ctreeSnapshot = null
    this.ctreeTree = null
    this.ctreeModel = createEmptyCTreeModel()
    this.ctreeTreeStatus = "idle"
    this.ctreeTreeError = null
    this.ctreeStage = "FROZEN"
    this.ctreeIncludePreviews = false
    this.ctreeSource = "auto"
    this.ctreeUpdatedAt = null
    this.ctreeTreeRequested = false
    this.ctreeRefreshPending = false
    this.ctreeRefreshInFlight = false
    if (this.ctreeRefreshTimer) {
      clearTimeout(this.ctreeRefreshTimer)
      this.ctreeRefreshTimer = null
    }
    this.lastEventId = null
    this.hasStreamedOnce = false
    this.resetTransientRuntimeState()
    this.activity = createActivitySnapshot("session")
    const requestedModel = this.config.model?.trim()
    const modelLabel = requestedModel ?? this.stats.model
    this.stats.model = modelLabel
    this.resolveProviderCapabilitiesSnapshot(modelLabel)
    const remotePreference = this.config.remotePreference ?? appConfig.remoteStreamDefault
    this.stats.remote = remotePreference
    const permissionValue = this.config.permissionMode?.trim()
    const permissionMode = permissionValue ? permissionValue.toLowerCase() : ""
    const interactivePermissions = permissionMode === "prompt" || permissionMode === "ask" || permissionMode === "interactive"
    this.permissionMode = normalizePermissionMode(permissionValue) ?? (interactivePermissions ? "prompt" : "auto")
    this.mode = normalizeModeValue(process.env.BREADBOARD_DEFAULT_MODE) ?? "build"
    const metadata: Record<string, unknown> = {}
    const overrides: Record<string, unknown> = {}
    const debugPermissions = process.env.BREADBOARD_DEBUG_PERMISSIONS
    if (debugPermissions && debugPermissions !== "0") {
      metadata.debug_permissions = true
    }
    if (permissionValue) {
      metadata.permission_mode = permissionValue
      if (interactivePermissions) {
        overrides["permissions.options.mode"] ??= "prompt"
        overrides["permissions.options.default_response"] ??= "reject"
        overrides["permissions.edit.default"] ??= "ask"
        overrides["permissions.shell.default"] ??= "ask"
        overrides["permissions.webfetch.default"] ??= "ask"
      }
    }
    if (requestedModel) {
      metadata.model = requestedModel
      overrides["providers.default_model"] = requestedModel
    }
    if (remotePreference) {
      metadata.enable_remote_stream = true
    }
    const payload = {
      config_path: this.config.configPath,
      task: "",
      workspace: this.config.workspace ?? undefined,
      permission_mode: permissionValue ?? undefined,
      metadata,
      overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
      stream: true,
    }
    const session = await this.api().createSession(payload)
    this.sessionId = session.session_id
    try {
      const summary = await this.api().getSession(this.sessionId)
      if (summary?.model && typeof summary.model === "string") {
        this.stats.model = summary.model
        this.resolveProviderCapabilitiesSnapshot(summary.model)
      }
      if (summary?.mode && typeof summary.mode === "string") {
        this.mode = normalizeModeValue(summary.mode) ?? this.mode
      }
      if (!summary?.model && !requestedModel) {
        const catalog = await getModelCatalog({ configPath: this.config.configPath })
        if (catalog?.defaultModel) {
          this.stats.model = catalog.defaultModel
          this.resolveProviderCapabilitiesSnapshot(catalog.defaultModel)
        }
      }
    } catch {
      // ignore summary fetch errors; keep optimistic defaults
    }
    try {
      const ctree = await this.api().getCtreeSnapshot(this.sessionId)
      this.ctreeSnapshot = (ctree ?? null) as unknown as CTreeSnapshot | null
    } catch {
      // ignore ctree bootstrap errors
    }
    this.pushHint(`Session ${this.sessionId} started (remote ${this.stats.remote ? "enabled" : "disabled"}, model ${this.stats.model}).`)
    this.setActivityStatus("Ready", { to: "idle", eventType: "session.ready", source: "runtime" })
    this.completionSeen = false
    this.lastCompletion = null
    this.stats.usage = undefined
    this.guardrailNotice = null
    this.submissionHistory = []
    this.emitChange()
    this.streamTask = this.streamLoop()
  }

  async stop(): Promise<void> {
    this.abortRequested = true
    this.abortController.abort()
    if (this.streamTask) {
      await this.streamTask.catch(() => undefined)
    }
    if (this.ctreeRefreshTimer) {
      clearTimeout(this.ctreeRefreshTimer)
      this.ctreeRefreshTimer = null
    }
    this.disposeAllMarkdown()
    this.setActivityStatus("Ready", { to: "idle", eventType: "session.stop", source: "runtime" })
  }

  async untilStopped(): Promise<void> {
    if (this.streamTask) {
      await this.streamTask.catch(() => undefined)
    }
  }

  async handleInput(text: string, attachments?: ReadonlyArray<QueuedAttachment>): Promise<void> {
    if (!this.sessionId) {
      this.pushHint("Session not ready yet. Please wait.")
      return
    }
    if (!text.trim()) {
      this.pushHint("Input is empty.")
      return
    }
    if (text.startsWith("/")) {
      const [command, ...args] = text.slice(1).split(/\s+/)
      await this.dispatchSlashCommand(command.toLowerCase(), args)
      this.emitChange()
      return
    }
    this.addConversation("user", text)
    this.pendingUserEcho = text.trim()
    try {
      const payload = await this.buildSubmissionPayload(text, attachments)
      await this.dispatchSubmission(payload, "Working…")
    } catch {
      this.pendingUserEcho = null
      // errors handled in dispatchSubmission
    }
  }

  private guessAttachmentFilename(mime: string, index: number): string {
    const suffix = (() => {
      if (mime.includes("png")) return "png"
      if (mime.includes("jpeg") || mime.includes("jpg")) return "jpg"
      if (mime.includes("gif")) return "gif"
      if (mime.includes("webp")) return "webp"
      return "bin"
    })()
    return `clipboard-${index + 1}.${suffix}`
  }

  private async tryUploadAttachments(attachments: ReadonlyArray<QueuedAttachment>): Promise<string[]> {
    if (attachments.length === 0) return []
    const summaryTarget = process.env.BREADBOARD_ATTACHMENT_SUMMARY_PATH
    const filenames: string[] = []
    try {
      const response = await this.providers.sdk.uploadAttachments(
        this.sessionId,
        attachments.map((attachment, index) => ({
          mime: attachment.mime,
          base64: attachment.base64,
          size: attachment.size,
          filename: (() => {
            const name = this.guessAttachmentFilename(attachment.mime, index)
            filenames.push(name)
            return name
          })(),
        })),
      )
      const ids = response.map((entry) => entry.id).filter((value): value is string => typeof value === "string" && value.length > 0)
      if (ids.length === 0) {
        this.pushHint("Attachment upload response contained no IDs; continuing without attachment references.")
      } else if (summaryTarget && summaryTarget.length > 0) {
        try {
          const entries = ids.map((id, index) => ({
            id,
            filename: filenames[index] ?? `attachment-${index + 1}`,
          }))
          const payload = {
            sessionId: this.sessionId,
            createdAt: new Date().toISOString(),
            count: ids.length,
            attachments: entries,
          }
          const resolved = path.isAbsolute(summaryTarget) ? summaryTarget : path.join(process.cwd(), summaryTarget)
          await fs.mkdir(path.dirname(resolved), { recursive: true })
          await fs.writeFile(resolved, `${JSON.stringify(payload, null, 2)}\n`, "utf8")
        } catch {
          // Best-effort summary only; ignore failures.
        }
      }
      return ids
    } catch (error) {
      if (error instanceof ApiError && (error.status === 404 || error.status === 501)) {
        this.pushHint("Backend does not yet implement attachment upload (404). Saved helper lines locally instead.")
        return []
      }
      this.pushHint(`Attachment upload failed: ${(error as Error).message}`)
      return []
    }
  }

  private async buildSubmissionPayload(content: string, attachments?: ReadonlyArray<QueuedAttachment>): Promise<SubmissionPayload> {
    let attachmentIds: string[] | undefined
    if (attachments && attachments.length > 0) {
      const ids = await this.tryUploadAttachments(attachments)
      if (ids.length > 0) {
        this.pushHint(`Uploaded ${ids.length} attachment${ids.length === 1 ? "" : "s"} to the engine.`)
        attachmentIds = ids
      }
    }
    return attachmentIds && attachmentIds.length > 0 ? { content, attachments: attachmentIds } : { content }
  }

  private async dispatchSubmission(payload: SubmissionPayload, statusLabel: string): Promise<void> {
    this.completionReached = false
    this.completionSeen = false
    this.lastCompletion = null
    this.removeLiveSlot("guardrail")
    this.pendingResponse = true
    this.setActivityStatus(statusLabel, { to: "run", eventType: "input.submit", source: "user" })
    if (this.pendingStartedAt == null) {
      this.pendingStartedAt = Date.now()
    }
    this.emitChange()
    try {
      await this.api().postInput(this.sessionId, payload)
      this.submissionHistory.push(payload)
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        this.pushHint("Backend does not yet support sending interactive input (404).")
      } else {
        this.pushHint(`Failed to send input: ${(error as Error).message}`)
      }
      this.pendingResponse = false
      this.setActivityStatus("Ready", { to: "idle", eventType: "input.error", source: "system" })
      this.emitChange()
      throw error
    }
  }

  private resetTransientRuntimeState(): void {
    this.runtimeTelemetry = createRuntimeTelemetry()
    this.activityTransitionTrace = []
    this.lastLifecycleToastAt = 0
    this.lastLifecycleToastMessage = null
    this.thinkingArtifact = null
    this.thinkingInlineEntryId = null
    this.lastThinkingPeekAt = 0
  }

  private resolveProviderCapabilitiesSnapshot(modelId: string | null): void {
    let overrideSchema: unknown = undefined
    const rawOverride = process.env.BREADBOARD_TUI_PROVIDER_CAPABILITIES_OVERRIDES
    if (rawOverride && rawOverride.trim().length > 0) {
      try {
        overrideSchema = JSON.parse(rawOverride)
      } catch {
        overrideSchema = "__invalid_json__"
      }
    }
    const runtimeOverrides = {
      rawThinkingPeek: this.runtimeFlags.allowRawThinkingPeek === true,
    }
    const result = resolveProviderCapabilities({
      modelId,
      preset: process.env.BREADBOARD_TUI_CAPABILITY_PRESET ?? null,
      overrideSchema,
      runtimeOverrides,
    })
    this.providerCapabilities = {
      provider: result.provider,
      model: result.model,
      reasoningEvents: result.capabilities.reasoningEvents,
      thoughtSummaryEvents: result.capabilities.thoughtSummaryEvents,
      contextUsage: result.capabilities.contextUsage,
      activitySurface: result.capabilities.activitySurface,
      rawThinkingPeek: result.capabilities.rawThinkingPeek,
      inlineThinkingBlock: result.capabilities.inlineThinkingBlock,
      warnings: result.warnings,
    }
    if (result.warnings.length > 0) {
      this.pushHint(`[capabilities] ${result.warnings[0]}`)
    }
  }

  private transitionActivity(input: ActivityTransitionInput): void {
    const previous = this.activity
    const result = reduceActivityTransition(this.activity, input, this.runtimeFlags)
    this.activityTransitionTrace.push({
      at: Number.isFinite(input.now as number) ? (input.now as number) : Date.now(),
      from: previous.primary,
      to: input.to,
      eventType: input.eventType ?? null,
      source: input.source ?? null,
      reason: result.reason,
      applied: result.applied,
    })
    if (this.activityTransitionTrace.length > 200) {
      this.activityTransitionTrace = this.activityTransitionTrace.slice(-200)
    }
    if (this.runtimeFlags.transitionDebug) {
      console.debug(
        `[activity] ${previous.primary} -> ${input.to} (${result.reason})${input.eventType ? ` event=${input.eventType}` : ""}`,
      )
    }
    if (result.applied) {
      this.runtimeTelemetry = bumpTelemetry(this.runtimeTelemetry, "statusTransitions")
      this.activity = result.snapshot
      return
    }
    if (result.reason === "illegal") {
      this.runtimeTelemetry = bumpTelemetry(this.runtimeTelemetry, "illegalTransitions")
    } else {
      this.runtimeTelemetry = bumpTelemetry(this.runtimeTelemetry, "suppressedTransitions")
    }
  }

  private setActivityStatus(
    status: string,
    transition?: Omit<ActivityTransitionInput, "label">,
  ): void {
    const now = Date.now()
    const withinCoalesceWindow =
      this.runtimeFlags.statusUpdateMs > 0 && now - this.statusUpdatedAt < this.runtimeFlags.statusUpdateMs
    const sameStatus = status === this.status
    const samePrimary = transition ? transition.to === this.activity.primary : true
    if (withinCoalesceWindow && sameStatus && samePrimary) {
      return
    }
    this.status = status
    this.statusUpdatedAt = now
    if (transition) {
      this.transitionActivity({ ...transition, label: status, now })
    }
  }

  private bumpRuntimeTelemetry(
    key: keyof RuntimeTelemetry,
    by = 1,
  ): void {
    this.runtimeTelemetry = bumpTelemetry(this.runtimeTelemetry, key, by)
  }

  async dispatchSlashCommand(command: string, args: string[]): Promise<void> {
    return dispatchSlashCommand.call(this, command, args)
  }

  async listFiles(path?: string): Promise<SessionFileInfo[]> {
    return listFiles.call(this, path)
  }

  async readFile(path: string, options?: ReadSessionFileOptions): Promise<SessionFileContent> {
    return readFile.call(this, path, options)
  }

  async openModelMenu(): Promise<void> {
    return openModelMenu.call(this)
  }

  async openSkillsMenu(): Promise<void> {
    return openSkillsMenu.call(this)
  }

  async openInspectMenu(): Promise<void> {
    return openInspectMenu.call(this)
  }

  async refreshInspectMenu(): Promise<void> {
    return refreshInspectMenu.call(this)
  }

  closeInspectMenu(): void {
    return closeInspectMenu.call(this)
  }

  closeSkillsMenu(): void {
    return closeSkillsMenu.call(this)
  }

  async applySkillsSelection(selection: SkillSelection): Promise<void> {
    return applySkillsSelection.call(this, selection)
  }

  closeModelMenu(): void {
    return closeModelMenu.call(this)
  }

  async selectModel(value: string): Promise<void> {
    return selectModel.call(this, value)
  }

  async runSessionCommand(command: string, payload?: Record<string, unknown>, successMessage?: string): Promise<boolean> {
    return runSessionCommand.call(this, command, payload, successMessage)
  }

  async loadModelMenuItems(): Promise<ModelMenuItem[]> {
    return loadModelMenuItems.call(this)
  }

  async waitFor(predicate: (state: ReplState) => boolean, timeoutMs = 10_000): Promise<ReplState> {
    return waitFor.call(this, predicate, timeoutMs)
  }

  async waitForCompletion(timeoutMs = 10_000): Promise<void> {
    return waitForCompletion.call(this, timeoutMs)
  }

  private slashHandlers(): Record<string, SlashHandler> {
    return stateMethods.slashHandlers.call(this)
  }

  private normalizeScope(value: unknown): PermissionRuleScope {
    return stateMethods.normalizeScope.call(this, value)
  }

  private parseCheckpointSummary(entry: unknown): CheckpointSummary | null {
    return stateMethods.parseCheckpointSummary.call(this, entry)
  }

  private extractTodosFromPayload(payload: unknown): TodoItem[] | null {
    return stateMethods.extractTodosFromPayload.call(this, payload)
  }

  async openRewindMenu(): Promise<void> {
    return stateMethods.openRewindMenu.call(this)
  }

  closeRewindMenu(): void {
    return stateMethods.closeRewindMenu.call(this)
  }

  async restoreCheckpoint(checkpointId: string, mode: "conversation" | "code" | "both"): Promise<boolean> {
    return stateMethods.restoreCheckpoint.call(this, checkpointId, mode)
  }

  private setPermissionActive(next: PermissionRequest | null): void {
    return stateMethods.setPermissionActive.call(this, next)
  }

  async respondToPermission(decision: PermissionDecision): Promise<boolean> {
    return stateMethods.respondToPermission.call(this, decision)
  }

  private addConversation(
    speaker: ConversationEntry["speaker"],
    text: string,
    phase: ConversationEntry["phase"] = "final",
  ): void {
    return stateMethods.addConversation.call(this, speaker, text, phase)
  }

  private setStreamingConversation(speaker: ConversationEntry["speaker"], text: string): void {
    return stateMethods.setStreamingConversation.call(this, speaker, text)
  }

  private finalizeStreamingEntry(): void {
    return stateMethods.finalizeStreamingEntry.call(this)
  }

  private nextConversationId(): string {
    return stateMethods.nextConversationId.call(this)
  }

  private upsertTask(entry: TaskEntry): void {
    return stateMethods.upsertTask.call(this, entry)
  }

  private handleTaskEvent(payload: Record<string, unknown>): void {
    return stateMethods.handleTaskEvent.call(this, payload)
  }

  private updateUsageFromPayload(payload: Record<string, unknown>): void {
    return stateMethods.updateUsageFromPayload.call(this, payload)
  }

  private trimToolHistory(): void {
    return stateMethods.trimToolHistory.call(this)
  }

  private addTool(
    kind: ToolLogKind,
    text: string,
    status?: LiveSlotStatus,
    options?: { callId?: string | null; insertAfterId?: string | null },
  ): ToolLogEntry {
    return stateMethods.addTool.call(this, kind, text, status, options)
  }

  private updateToolEntry(
    entryId: string,
    patch: Partial<Omit<ToolLogEntry, "id" | "createdAt">>,
  ): ToolLogEntry | null {
    return stateMethods.updateToolEntry.call(this, entryId, patch)
  }

  private formatToolSlot(payload: unknown): { text: string; color?: string; summary?: string } {
    return stateMethods.formatToolSlot.call(this, payload)
  }

  private resolveToolDisplayPayload(payload: Record<string, unknown>): Record<string, unknown> | null {
    return stateMethods.resolveToolDisplayPayload.call(this, payload)
  }

  private formatToolDisplayText(payload: Record<string, unknown>): string {
    return stateMethods.formatToolDisplayText.call(this, payload)
  }

  private resolveToolCallId(payload: Record<string, unknown>): string | null {
    return stateMethods.resolveToolCallId.call(this, payload)
  }

  private appendToolCallArgs(callId: string, delta: string): string {
    return stateMethods.appendToolCallArgs.call(this, callId, delta)
  }

  private appendToolExecOutput(callId: string, stream: "stdout" | "stderr", chunk: string): void {
    return stateMethods.appendToolExecOutput.call(this, callId, stream, chunk)
  }

  private takeToolExecOutput(callId: string): { stdout: string; stderr: string } | null {
    return stateMethods.takeToolExecOutput.call(this, callId)
  }

  private formatToolExecOutput(output: { stdout: string; stderr: string } | null): string | null {
    return stateMethods.formatToolExecOutput.call(this, output)
  }

  private formatToolExecPreview(output: { stdout: string; stderr: string } | null): string | null {
    return stateMethods.formatToolExecPreview.call(this, output)
  }

  private extractDiffSummary(payload: unknown): string | undefined {
    return stateMethods.extractDiffSummary.call(this, payload)
  }

  private shouldStreamMarkdown(): boolean {
    return renderState.shouldStreamMarkdown.call(this)
  }

  private ensureMarkdownStreamer(entryId: string): { streamer: MarkdownStreamer; lastText: string } | null {
    return renderState.ensureMarkdownStreamer.call(this, entryId)
  }

  private appendMarkdownChunk(text: string): void {
    return renderState.appendMarkdownChunk.call(this, text)
  }

  private appendMarkdownDelta(delta: string): void {
    return renderState.appendMarkdownDelta.call(this, delta)
  }

  private finalizeMarkdown(entryId: string | null): void {
    return renderState.finalizeMarkdown.call(this, entryId)
  }

  private disposeAllMarkdown(): void {
    return renderState.disposeAllMarkdown.call(this)
  }

  private applyMarkdownBlocks(entryId: string, blocks: ReadonlyArray<Block>, error: string | null, finalized: boolean): void {
    return renderState.applyMarkdownBlocks.call(this, entryId, blocks, error, finalized)
  }

  private markEntryMarkdownStreaming(entryId: string, streaming: boolean): void {
    return renderState.markEntryMarkdownStreaming.call(this, entryId, streaming)
  }

  private upsertLiveSlot(
    id: string,
    text: string,
    color?: string,
    status: LiveSlotStatus = "pending",
    stickyMs?: number,
    summary?: string,
  ): void {
    return renderState.upsertLiveSlot.call(this, id, text, color, status, stickyMs, summary)
  }

  private finalizeLiveSlot(
    id: string,
    status: LiveSlotStatus,
    fallbackText?: string,
    fallbackColor?: string,
    summary?: string,
  ): void {
    return renderState.finalizeLiveSlot.call(this, id, status, fallbackText, fallbackColor, summary)
  }

  private clearLiveSlotTimer(id: string): void {
    return renderState.clearLiveSlotTimer.call(this, id)
  }

  private removeLiveSlot(id: string): void {
    return renderState.removeLiveSlot.call(this, id)
  }

  private setGuardrailNotice(summary: string, detail?: string): void {
    return renderState.setGuardrailNotice.call(this, summary, detail)
  }

  private clearGuardrailNotice(): void {
    return renderState.clearGuardrailNotice.call(this)
  }

  toggleGuardrailNotice(): void {
    return renderState.toggleGuardrailNotice.call(this)
  }

  dismissGuardrailNotice(): void {
    return renderState.dismissGuardrailNotice.call(this)
  }

  private updateViewPrefs(update: Partial<TranscriptPreferences>, message?: string): void {
    return renderState.updateViewPrefs.call(this, update, message)
  }

  private isToolResultError(payload: unknown): boolean {
    return stateMethods.isToolResultError.call(this, payload)
  }

  private pushHint(msg: string): void {
    return stateMethods.pushHint.call(this, msg)
  }

  private pushRawEvent(event: SessionEvent): void {
    return stateMethods.pushRawEvent.call(this, event)
  }

  private emitChange(): void {
    return stateMethods.emitChange.call(this)
  }

  private handleToolCall(
    payload: Record<string, unknown>,
    callIdOverride?: string | null,
    allowExisting = true,
    logEntry = true,
  ): string | null {
    return eventMethods.handleToolCall.call(this, payload, callIdOverride, allowExisting, logEntry)
  }

  private handleToolResult(payload: Record<string, unknown>, callIdOverride?: string | null): void {
    return eventMethods.handleToolResult.call(this, payload, callIdOverride)
  }

  private async streamLoop(): Promise<void> {
    return eventMethods.streamLoop.call(this)
  }

  async refreshCtreeTree(options?: {
    readonly stage?: CTreeTreeStage | string
    readonly includePreviews?: boolean
    readonly source?: CTreeTreeSource | string
    readonly silent?: boolean
  }): Promise<void> {
    return ctreeMethods.refreshCtreeTree.call(this, options)
  }

  async requestCtreeTree(force = false): Promise<void> {
    return ctreeMethods.requestCtreeTree.call(this, force)
  }

  async setCtreeStage(stage: CTreeTreeStage | string): Promise<void> {
    return ctreeMethods.setCtreeStage.call(this, stage)
  }

  async setCtreeSource(source: CTreeTreeSource | string): Promise<void> {
    return ctreeMethods.setCtreeSource.call(this, source)
  }

  async setCtreePreviews(includePreviews: boolean): Promise<void> {
    return ctreeMethods.setCtreePreviews.call(this, includePreviews)
  }

  private scheduleCtreeRefresh(): void {
    return ctreeMethods.scheduleCtreeRefresh.call(this)
  }

  private enqueueEvent(event: SessionEvent): void {
    return eventMethods.enqueueEvent.call(this, event)
  }

  private applyEvent(event: SessionEvent): void {
    return eventMethods.applyEvent.call(this, event)
  }

  private normalizeAssistantText(text: string): string {
    return eventMethods.normalizeAssistantText.call(this, text)
  }

  private appendAssistantDelta(delta: string): void {
    return eventMethods.appendAssistantDelta.call(this, delta)
  }

  private noteStopRequested(): void {
    return eventMethods.noteStopRequested.call(this)
  }

  private clearStopRequest(): void {
    return eventMethods.clearStopRequest.call(this)
  }

}
