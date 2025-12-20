import { EventEmitter } from "node:events"
import { promises as fs } from "node:fs"
import path from "node:path"
import type { Block } from "@stream-mdx/core/types"
import type {
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
  PermissionRequest,
  PermissionDecision,
  PermissionRuleScope,
  RewindMenuState,
  CheckpointSummary,
} from "../../repl/types.js"
import { SLASH_COMMANDS } from "../../repl/slashCommands.js"
import { ApiError } from "../../api/client.js"
import type { ReadSessionFileOptions } from "../../api/client.js"
import type { SessionEvent, SessionFileInfo, SessionFileContent } from "../../api/types.js"
import { DEFAULT_MODEL_ID, loadAppConfig } from "../../config/appConfig.js"
import { getModelCatalog } from "../../providers/modelCatalog.js"
import { CliProviders } from "../../providers/cliProviders.js"
import { computeDiffPreview } from "../../repl/transcriptUtils.js"
import { MarkdownStreamer } from "../../markdown/streamer.js"

const MAX_HINTS = 6
const MAX_TOOL_HISTORY = 400
const MAX_RETRIES = 5
const DEBUG_EVENTS = process.env.BREADBOARD_DEBUG_EVENTS === "1"
const DEBUG_WAIT = process.env.BREADBOARD_DEBUG_WAIT === "1"
const DEBUG_MARKDOWN = process.env.BREADBOARD_DEBUG_MARKDOWN === "1"
const DEFAULT_RICH_MARKDOWN = process.env.BREADBOARD_RICH_MARKDOWN !== "0"

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

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
  readonly sessionId: string
  readonly status: string
  readonly pendingResponse: boolean
  readonly conversation: ConversationEntry[]
  readonly toolEvents: ToolLogEntry[]
  readonly liveSlots: LiveSlotEntry[]
  readonly hints: string[]
  readonly stats: StreamStats
  readonly modelMenu: ModelMenuState
  readonly completionReached: boolean
  readonly completionSeen: boolean
  readonly lastCompletion?: CompletionState | null
  readonly disconnected: boolean
  readonly guardrailNotice?: GuardrailNotice | null
  readonly viewPrefs: TranscriptPreferences
  readonly permissionRequest?: PermissionRequest | null
  readonly permissionQueueDepth?: number
  readonly rewindMenu: RewindMenuState
}

type StateListener = (state: ReplState) => void

type SlashHandler = (args: string[]) => Promise<void>

interface SubmissionPayload {
  readonly content: string
  readonly attachments?: ReadonlyArray<string>
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null

const stringifyReason = (value: string | undefined): string | undefined =>
  value?.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || undefined

const numberOrUndefined = (value: unknown): number | undefined => (typeof value === "number" && Number.isFinite(value) ? value : undefined)
const createSlotId = (): string => `slot-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`

const extractString = (payload: Record<string, unknown>, keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = payload[key]
    if (typeof value === "string" && value.trim()) return value.trim()
  }
  return undefined
}

const extractProgress = (payload: Record<string, unknown>): number | undefined => {
  const candidate = payload.progress_pct ?? payload.progress ?? payload.percentage
  if (typeof candidate === "number") return candidate
  if (typeof candidate === "string") {
    const parsed = Number(candidate.replace(/%$/, ""))
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

export const formatCompletion = (payload: unknown): CompletionView => {
  const container = isRecord(payload) ? payload : {}
  const summarySource = isRecord(container.summary) ? container.summary : container
  const completed = summarySource.completed === true || summarySource.status === "completed" || summarySource.success === true
  const exitKind = stringifyReason(typeof summarySource.exit_kind === "string" ? summarySource.exit_kind : undefined)
  const reason = stringifyReason(typeof summarySource.reason === "string" ? summarySource.reason : undefined)
  const method = stringifyReason(typeof summarySource.method === "string" ? summarySource.method : undefined)
  const message = stringifyReason(typeof summarySource.message === "string" ? summarySource.message : undefined)
  const note = stringifyReason(typeof summarySource.note === "string" ? summarySource.note : undefined)
  const summaryText = stringifyReason(typeof summarySource.summary === "string" ? summarySource.summary : undefined)
  const stepsTaken = numberOrUndefined(summarySource.steps_taken)
  const maxSteps = numberOrUndefined(summarySource.max_steps)
  const providerMode = stringifyReason(typeof container.mode === "string" ? container.mode : undefined)

  const reasonTokens = [exitKind, reason, method].filter((value, index, array): value is string => Boolean(value) && array.indexOf(value) === index)
  const reasonTokensLower = reasonTokens.map((value) => value.toLowerCase())
  const reasonText = reasonTokens.length > 0 ? reasonTokens.join(" · ") : undefined
  const stepsText = stepsTaken != null ? `steps ${stepsTaken}${maxSteps != null ? `/${maxSteps}` : ""}` : undefined
  const statusLabel = completed ? "Completed" : "Awaiting input"

  const toolParts: string[] = [`status ${completed ? "completed" : "stopped"}`]
  if (reasonText) toolParts.push(`reason ${reasonText}`)
  if (stepsText) toolParts.push(stepsText)
  if (providerMode) toolParts.push(`mode ${providerMode}`)

  let conversationLine: string | undefined
  let hint = summaryText || message || note

  if (completed) {
    conversationLine = reasonText ? `Run completed (${reasonText}).` : "Run completed successfully."
    if (!hint) hint = "Assistant finished the run."
  } else if (reasonTokensLower.some((token) => token.includes("policy violation"))) {
    conversationLine = "Run halted — provider flagged a policy violation. Try rephrasing or switching models."
    if (!hint) hint = "Policy violation reported. Consider /model or adjusting your prompt."
  } else if (reasonText) {
    conversationLine = `Run halted — ${reasonText}.`
    if (!hint) hint = `Run halted: ${reasonText}.`
  } else {
    conversationLine = "Run halted."
    if (!hint) hint = "Run halted. You can adjust your instructions or /retry."
  }

  return {
    completed,
    status: statusLabel,
    toolLine: toolParts.join(" · "),
    hint,
    conversationLine,
    warningSlot:
      reasonTokensLower.some((token) => token.includes("policy violation")) && !completed
        ? {
            text: "Guardrail warning: provider flagged a possible policy violation.",
            color: "#fb7185",
          }
        : undefined,
  }
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
  private readonly hints: string[] = []
  private conversationSequence = 0
  private streamingEntryId: string | null = null
  private viewPrefs: TranscriptPreferences = { collapseMode: "auto", virtualization: "auto", richMarkdown: DEFAULT_RICH_MARKDOWN }
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
  private pendingResponse = false
  private modelMenu: ModelMenuState = { status: "hidden" }
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
  private markdownGloballyDisabled = false
  private permissionActive: PermissionRequest | null = null
  private permissionQueue: PermissionRequest[] = []
  private rewindMenu: RewindMenuState = { status: "hidden" }

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
      sessionId: this.sessionId,
      status: this.status,
      pendingResponse: this.pendingResponse,
      conversation: [...this.conversation],
      toolEvents: [...this.toolEvents],
      liveSlots: liveSlotList,
      hints: [...this.hints],
      stats: { ...this.stats },
      modelMenu: this.modelMenu,
      completionReached: this.completionReached,
      completionSeen: this.completionSeen,
      lastCompletion: this.lastCompletion,
      disconnected: this.disconnected,
      guardrailNotice: this.guardrailNotice,
      viewPrefs: this.viewPrefs,
      permissionRequest: this.permissionActive,
      permissionQueueDepth: this.permissionQueue.length,
      rewindMenu: this.rewindMenu,
    }
  }

  onChange(listener: StateListener): () => void {
    this.on("change", listener)
    listener(this.getState())
    return () => this.off("change", listener)
  }

  async start(): Promise<void> {
    const appConfig = this.providers.args.config
    const requestedModel = this.config.model?.trim()
    const modelLabel = requestedModel ?? this.stats.model
    this.stats.model = modelLabel
    const remotePreference = this.config.remotePreference ?? appConfig.remoteStreamDefault
    this.stats.remote = remotePreference
    const metadata: Record<string, unknown> = {}
    const overrides: Record<string, unknown> = {}
    if (this.config.permissionMode) {
      metadata.permission_mode = this.config.permissionMode
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
      metadata,
      overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
      stream: true,
    }
    const session = await this.api().createSession(payload)
    this.sessionId = session.session_id
    this.pushHint(`Session ${this.sessionId} started (remote ${this.stats.remote ? "enabled" : "disabled"}, model ${this.stats.model}).`)
    this.status = "Ready"
    this.completionSeen = false
    this.lastCompletion = null
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
    this.disposeAllMarkdown()
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
    try {
      const payload = await this.buildSubmissionPayload(text, attachments)
      await this.dispatchSubmission(payload, "Working…")
    } catch {
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
    this.status = statusLabel
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
      this.status = "Ready"
      this.emitChange()
      throw error
    }
  }

  async dispatchSlashCommand(command: string, args: string[]): Promise<void> {
    const handler = this.slashHandlers()[command]
    if (!handler) {
      this.pushHint(`Unknown command: /${command}`)
      return
    }
    await handler(args)
  }

  async listFiles(path?: string): Promise<SessionFileInfo[]> {
    if (!this.sessionId) {
      throw new Error("Session not ready yet.")
    }
    try {
      return await this.api().listSessionFiles(this.sessionId, path)
    } catch (error) {
      if (error instanceof ApiError) {
        throw new Error(`File listing failed (${error.status}).`)
      }
      throw error
    }
  }

  async readFile(path: string, options?: ReadSessionFileOptions): Promise<SessionFileContent> {
    if (!this.sessionId) {
      throw new Error("Session not ready yet.")
    }
    if (!path || !path.trim()) {
      throw new Error("File path is empty.")
    }
    try {
      return await this.api().readSessionFile(this.sessionId, path, options)
    } catch (error) {
      if (error instanceof ApiError) {
        throw new Error(`File read failed (${error.status}).`)
      }
      throw error
    }
  }

  async openModelMenu(): Promise<void> {
    if (this.modelMenu.status !== "hidden") {
      this.pushHint("Model picker already open. Use Esc to cancel or select a model.")
      return
    }
    this.status = "Loading models…"
    this.modelMenu = { status: "loading" }
    this.emitChange()
    try {
      const items = await this.loadModelMenuItems()
      if (items.length === 0) {
        this.modelMenu = { status: "error", message: "No models available for active credentials." }
        this.status = "No models available"
      } else {
        this.modelMenu = { status: "ready", items }
        this.pushHint("Use selectModel action or interactive picker to choose a model.")
        this.status = "Model picker ready"
      }
    } catch (error) {
      this.modelMenu = { status: "error", message: `Failed to load models: ${(error as Error).message}` }
      this.status = "Model catalog unavailable"
    }
    this.emitChange()
  }

  closeModelMenu(): void {
    if (this.modelMenu.status !== "hidden") {
      this.modelMenu = { status: "hidden" }
      this.emitChange()
    }
  }

  async selectModel(value: string): Promise<void> {
    this.closeModelMenu()
    await this.runSessionCommand("set_model", { model: value }, `Model switch requested (${value}).`)
    this.status = `Model request: ${value}`
    this.emitChange()
  }

  async runSessionCommand(command: string, payload?: Record<string, unknown>, successMessage?: string): Promise<boolean> {
    if (!this.sessionId) {
      this.pushHint("Session not ready yet.")
      return false
    }
    const body: Record<string, unknown> = { command }
    if (payload && Object.keys(payload).length > 0) body.payload = payload
    try {
      await this.api().postCommand(this.sessionId, body)
      if (command === "set_model") {
        const value = payload?.model
        if (typeof value === "string") {
          this.stats.model = value
        }
      }
      this.addTool("command", `[command] ${command}${payload ? ` ${JSON.stringify(payload)}` : ""}`)
      this.pushHint(successMessage ?? `Sent command "${command}".`)
      return true
    } catch (error) {
      if (error instanceof ApiError) {
        this.pushHint(`Command failed (${error.status}).`)
      } else {
        this.pushHint(`Command error: ${(error as Error).message}`)
      }
      return false
    }
  }

  async loadModelMenuItems(): Promise<ModelMenuItem[]> {
    const models = await getModelCatalog()
    return models
      .map<ModelMenuItem>((model) => {
        const providerLabel = model.provider === "openrouter" ? "OpenRouter" : "OpenAI"
        const contextTokens = typeof model.contextLength === "number" ? model.contextLength : null
        const contextK = contextTokens != null ? Math.max(1, Math.round(contextTokens / 1000)) : null
        const priceInPerM = model.priceInPerM ?? null
        const priceOutPerM = model.priceOutPerM ?? null
        const detailParts: string[] = []
        if (contextK != null) detailParts.push(`${contextK}k ctx`)
        if (priceInPerM != null) detailParts.push(`in $${priceInPerM.toFixed(2)}`)
        if (priceOutPerM != null) detailParts.push(`out $${priceOutPerM.toFixed(2)}`)
        const detail = detailParts.length > 0 ? detailParts.join(" • ") : model.pricing
        return {
          label: `${providerLabel} · ${model.name}`,
          value: model.id,
          provider: model.provider,
          detail,
          contextTokens,
          priceInPerM,
          priceOutPerM,
          isDefault: model.id === DEFAULT_MODEL_ID,
          isCurrent: model.id === this.stats.model,
        }
      })
      .sort((a, b) => {
        if (a.isCurrent && !b.isCurrent) return -1
        if (!a.isCurrent && b.isCurrent) return 1
        if (a.isDefault && !b.isDefault) return -1
        if (!a.isDefault && b.isDefault) return 1
        return a.label.localeCompare(b.label)
      })
  }

  async waitFor(predicate: (state: ReplState) => boolean, timeoutMs = 10_000): Promise<ReplState> {
    const limit = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 10_000
    const start = Date.now()
    const deadline = start + limit
    if (DEBUG_WAIT) {
      console.log(`[repl wait] waiting up to ${limit}ms (session ${this.sessionId || "pending"})`)
    }
    return await new Promise<ReplState>((resolve, reject) => {
      const check = (state: ReplState) => {
        if (predicate(state)) {
          cleanup()
          if (DEBUG_WAIT) {
            console.log(`[repl wait] predicate satisfied after ${Date.now() - start}ms`)
          }
          resolve(state)
        } else if (Date.now() > deadline) {
          cleanup()
          if (DEBUG_WAIT) {
            console.log(`[repl wait] predicate timeout after ${Date.now() - start}ms`)
          }
          reject(new Error("waitFor timeout reached"))
        }
      }
      const timer = setInterval(() => {
        const state = this.getState()
        if (predicate(state)) {
          cleanup()
          if (DEBUG_WAIT) {
            console.log(`[repl wait] predicate satisfied via poll after ${Date.now() - start}ms`)
          }
          resolve(state)
        } else if (Date.now() > deadline) {
          cleanup()
          if (DEBUG_WAIT) {
            console.log(`[repl wait] predicate timeout via poll after ${Date.now() - start}ms`)
          }
          reject(new Error("waitFor timeout reached"))
        }
      }, 50)
      const cleanup = () => {
        clearInterval(timer)
        this.off("change", check)
      }
      this.on("change", check)
      check(this.getState())
    })
  }

  async waitForCompletion(timeoutMs = 10_000): Promise<void> {
    const limit = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 10_000
    try {
      await this.waitFor((state) => state.completionSeen, limit)
      return
    } catch (error) {
      if (!this.sessionId) throw error
      const pollWindowMs = Math.min(Math.max(Math.floor(limit / 2), 5_000), 30_000)
      const pollIntervalMs = 750
      const deadline = Date.now() + pollWindowMs
      let attempts = 0
      while (Date.now() <= deadline) {
        attempts += 1
        try {
          const summary = await this.api().getSession(this.sessionId)
          if (summary.completion_summary) {
            this.completionSeen = true
            this.lastCompletion = {
              completed: summary.completion_summary.completed === true,
              summary: summary.completion_summary,
            }
            if (DEBUG_WAIT) {
              console.log(
                `[repl wait] completion detected via fallback`,
                JSON.stringify({ session: this.sessionId, attempts }),
              )
            }
            this.emitChange()
            return
          }
        } catch {
          // ignore fetch errors during polling; we'll retry within the window
        }
        await sleep(pollIntervalMs)
      }
      if (DEBUG_WAIT) {
        console.error(
          `[repl wait] completion timeout`,
          JSON.stringify({
            session: this.sessionId,
            completionSeen: this.completionSeen,
            status: this.status,
            disconnected: this.disconnected,
            eventCount: this.stats.eventCount,
            pollAttempts: attempts,
          }),
        )
      }
      throw error
    }
  }

  private slashHandlers(): Record<string, SlashHandler> {
    return {
      quit: async () => {
        this.pushHint("Exiting session…")
        this.status = "Exiting…"
        await this.stop()
      },
      stop: async () => {
        const ok = await this.runSessionCommand("stop", undefined, "Interrupt requested.")
        if (ok) {
          this.status = "Interrupt requested"
        }
      },
      help: async () => {
        const summary = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""} — ${entry.summary}`).join(" | ")
        this.pushHint(summary)
      },
      clear: async () => {
        this.conversation.length = 0
        this.toolEvents.length = 0
        this.submissionHistory = []
        this.streamingEntryId = null
        this.disposeAllMarkdown()
        this.markdownGloballyDisabled = false
        this.pushHint("Cleared conversation and tool logs.")
      },
      status: async () => {
        try {
          const summary = await this.api().getSession(this.sessionId)
          this.pushHint(`Status: ${summary.status}, last activity ${summary.last_activity_at}`)
          this.status = `Status: ${summary.status}`
          if (summary.completion_summary) {
            this.addTool("status", `[status] completion ${JSON.stringify(summary.completion_summary)}`)
          }
        } catch (error) {
          this.pushHint(`Status check failed: ${(error as Error).message}`)
        }
      },
      remote: async (args) => {
        const action = args[0]?.toLowerCase()
        if (action === "on") {
          this.stats.remote = true
          this.pushHint("Remote streaming preference enabled (next session).")
          this.status = "Remote preference: on"
        } else if (action === "off") {
          this.stats.remote = false
          this.pushHint("Remote streaming preference disabled (next session).")
          this.status = "Remote preference: off"
        } else {
          this.pushHint(`Remote currently ${this.stats.remote ? "enabled" : "disabled"}. Use /remote on|off.`)
        }
      },
      retry: async (args) => {
        if (this.pendingResponse) {
          this.pushHint("Already waiting for a response. Try /retry after the current turn completes.")
          return
        }
        const offset = args[0] ? Number(args[0]) : 1
        if (!Number.isFinite(offset) || offset <= 0) {
          this.pushHint("Usage: /retry [n] — resubmits the nth most recent prompt (default 1).")
          return
        }
        const index = this.submissionHistory.length - offset
        if (index < 0 || index >= this.submissionHistory.length) {
          this.pushHint("No matching submission to retry yet.")
          return
        }
        const payload = this.submissionHistory[index]
        this.pushHint(`Resubmitting prompt #${this.submissionHistory.length - index}${offset === 1 ? "" : " (offset)"}…`)
        this.addConversation("user", payload.content)
        try {
          await this.dispatchSubmission(payload, "Retrying prior input…")
        } catch {
          // handled in dispatchSubmission
        }
      },
      plan: async () => {
        const ok = await this.runSessionCommand("set_mode", { mode: "plan" }, "Requested plan-focused mode.")
        if (ok) this.status = "Mode request: plan"
      },
      mode: async (args) => {
        const target = args[0]?.toLowerCase()
        if (!target) {
          this.pushHint("Usage: /mode <plan|build|auto>. Alias: /plan.")
          return
        }
        if (!["plan", "build", "auto", "default"].includes(target)) {
          this.pushHint(`Unknown mode "${target}". Expected plan, build, or auto.`)
          return
        }
        const normalized = target === "default" ? "auto" : target
        const ok = await this.runSessionCommand("set_mode", { mode: normalized }, `Mode set request sent (${normalized}).`)
        if (ok) this.status = `Mode request: ${normalized}`
      },
      model: async (args) => {
        const newModel = args[0]
        if (!newModel) {
          this.pushHint("Usage: /model <provider/model-id>")
          return
        }
        const ok = await this.runSessionCommand("set_model", { model: newModel }, `Model switch requested (${newModel}).`)
        if (ok) this.status = `Model request: ${newModel}`
      },
      test: async (args) => {
        const suite = args.join(" ").trim()
        const payload = suite.length > 0 ? { suite } : undefined
        const ok = await this.runSessionCommand(
          "run_tests",
          payload,
          suite.length > 0 ? `Test suite requested (${suite}).` : "Default test suite requested.",
        )
        if (ok) this.status = suite.length > 0 ? `Test request: ${suite}` : "Test request: default"
      },
      view: async (args) => {
        const scope = args[0]?.toLowerCase()
        if (!scope) {
          this.pushHint(
            `View prefs — collapse: ${this.viewPrefs.collapseMode}, scroll: ${this.viewPrefs.virtualization}, markdown: ${this.viewPrefs.richMarkdown ? "on" : "off"}. Usage: /view collapse <auto|all|none>, /view scroll <auto|compact>, or /view markdown <on|off>.`,
          )
          return
        }
        if (scope === "collapse" || scope === "collapses") {
          const value = args[1]?.toLowerCase()
          if (!value || !["auto", "all", "none"].includes(value)) {
            this.pushHint("Usage: /view collapse <auto|all|none>.")
            return
          }
          const normalized = value === "all" ? "all" : value === "none" ? "none" : "auto"
          this.updateViewPrefs({ collapseMode: normalized }, `Collapse mode set to ${normalized}.`)
          return
        }
        if (["scroll", "virtual", "virtualization", "mode"].includes(scope)) {
          const value = args[1]?.toLowerCase()
          if (!value || !["auto", "compact", "log"].includes(value)) {
            this.pushHint("Usage: /view scroll <auto|compact>. Compact limits transcript to the visible log window.")
            return
          }
          const normalized = value === "compact" ? "compact" : "auto"
          this.updateViewPrefs({ virtualization: normalized }, `Scroll mode set to ${normalized}.`)
          return
        }
        if (["markdown", "md", "rich"].includes(scope)) {
          const value = args[1]?.toLowerCase()
          if (!value || !["on", "off"].includes(value)) {
            this.pushHint(`Usage: /view markdown <on|off>. Currently ${this.viewPrefs.richMarkdown ? "on" : "off"}.`)
            return
          }
          const enabled = value === "on"
          this.markdownGloballyDisabled = false
          this.updateViewPrefs({ richMarkdown: enabled }, `Rich markdown ${enabled ? "enabled" : "disabled"}.`)
          return
        }
        this.pushHint("Usage: /view collapse <auto|all|none>, /view scroll <auto|compact>, or /view markdown <on|off>.")
      },
      files: async (args) => {
        const scope = args[0] ?? "."
        try {
          const files = await this.api().listSessionFiles(this.sessionId, scope === "." ? undefined : scope)
          const output = files
            .map((file) => `${file.type.padEnd(4, " ")} ${file.path}${file.size != null ? ` ${file.size}` : ""}`)
            .join("\n")
          this.pushHint(`Files listed for ${scope === "." ? "(root)" : scope}.`)
          this.addTool("status", `[files]\n${output}`)
        } catch (error) {
          if (error instanceof ApiError) {
            this.pushHint(`File listing failed (${error.status}).`)
          } else {
            this.pushHint(`File listing error: ${(error as Error).message}`)
          }
        }
      },
      models: async () => {
        await this.openModelMenu()
      },
      rewind: async () => {
        await this.openRewindMenu()
      },
    }
  }

  private normalizeScope(value: unknown): PermissionRuleScope {
    switch (String(value ?? "").toLowerCase()) {
      case "session":
        return "session"
      case "global":
        return "global"
      default:
        return "project"
    }
  }

  private parseCheckpointSummary(entry: unknown): CheckpointSummary | null {
    if (!isRecord(entry)) return null
    const checkpointId =
      typeof entry.checkpoint_id === "string"
        ? entry.checkpoint_id
        : typeof entry.id === "string"
          ? entry.id
          : null
    if (!checkpointId) return null
    const createdAtRaw =
      typeof entry.created_at === "number"
        ? entry.created_at
        : typeof entry.timestamp === "number"
          ? entry.timestamp
          : Date.now()
    const createdAt = createdAtRaw > 10_000_000_000 ? createdAtRaw : createdAtRaw * 1000
    const preview = typeof entry.preview === "string" ? entry.preview : typeof entry.prompt === "string" ? entry.prompt : checkpointId
    const trackedFiles = typeof entry.tracked_files === "number" ? entry.tracked_files : typeof entry.trackedFiles === "number" ? entry.trackedFiles : null
    const additions = typeof entry.additions === "number" ? entry.additions : null
    const deletions = typeof entry.deletions === "number" ? entry.deletions : null
    const hasUntrackedChanges = typeof entry.has_untracked_changes === "boolean" ? entry.has_untracked_changes : null
    return {
      checkpointId,
      createdAt,
      preview,
      trackedFiles,
      additions,
      deletions,
      hasUntrackedChanges,
    }
  }

  async openRewindMenu(): Promise<void> {
    if (!this.sessionId) {
      this.pushHint("Session not ready yet.")
      return
    }
    const existing =
      this.rewindMenu.status === "ready" || this.rewindMenu.status === "error" ? this.rewindMenu.checkpoints : []
    this.rewindMenu = { status: "loading", checkpoints: existing }
    this.status = "Loading checkpoints…"
    this.emitChange()
    const ok = await this.runSessionCommand("list_checkpoints", undefined, "Requested checkpoint list.")
    if (!ok) {
      this.rewindMenu = { status: "error", message: "Failed to request checkpoints.", checkpoints: existing }
      this.status = "Checkpoint list unavailable"
      this.emitChange()
    }
  }

  closeRewindMenu(): void {
    if (this.rewindMenu.status !== "hidden") {
      this.rewindMenu = { status: "hidden" }
      this.emitChange()
    }
  }

  async restoreCheckpoint(checkpointId: string, mode: "conversation" | "code" | "both"): Promise<boolean> {
    if (!this.sessionId) {
      this.pushHint("Session not ready yet.")
      return false
    }
    const ok = await this.runSessionCommand(
      "restore_checkpoint",
      { checkpoint_id: checkpointId, mode },
      `Requested restore (${mode}).`,
    )
    if (ok) {
      this.status = `Restoring (${mode})…`
      this.emitChange()
    }
    return ok
  }

  private setPermissionActive(next: PermissionRequest | null): void {
    this.permissionActive = next
    if (!next) {
      if (this.permissionQueue.length > 0) {
        this.permissionActive = this.permissionQueue.shift() ?? null
      }
    }
  }

  async respondToPermission(decision: PermissionDecision): Promise<boolean> {
    if (!this.sessionId) {
      this.pushHint("Session not ready yet.")
      return false
    }
    if (!this.permissionActive) {
      this.pushHint("No permission request is currently active.")
      return false
    }
    const requestId = this.permissionActive.requestId
    const payload: Record<string, unknown> = { request_id: requestId, decision: decision.kind }
    if (decision.kind === "allow-always" || decision.kind === "deny-always") {
      payload.scope = decision.scope
      if (decision.rule != null) payload.rule = decision.rule
    }
    if (decision.kind === "deny-stop") {
      payload.stop = true
    }
    const ok = await this.runSessionCommand("permission_decision", payload, "Permission decision sent.")
    if (ok) {
      if (decision.kind === "deny-stop") {
        this.pendingResponse = false
        this.status = "Stopped (permission denied)"
        this.permissionQueue.length = 0
        this.permissionActive = null
      } else {
        this.setPermissionActive(null)
      }
      this.emitChange()
    }
    return ok
  }

  private addConversation(
    speaker: ConversationEntry["speaker"],
    text: string,
    phase: ConversationEntry["phase"] = "final",
  ): void {
    if (phase === "streaming") {
      this.setStreamingConversation(speaker, text)
      return
    }
    this.finalizeStreamingEntry()
    const entry: ConversationEntry = {
      id: this.nextConversationId(),
      speaker,
      text,
      phase: "final",
    }
    this.conversation.push(entry)
  }

  private setStreamingConversation(speaker: ConversationEntry["speaker"], text: string): void {
    if (this.streamingEntryId) {
      const index = this.conversation.findIndex((entry) => entry.id === this.streamingEntryId)
      if (index >= 0) {
        const existing = this.conversation[index]
        this.conversation[index] = { ...existing, speaker, text, phase: "streaming" }
        return
      }
    }
    const entry: ConversationEntry = {
      id: this.nextConversationId(),
      speaker,
      text,
      phase: "streaming",
    }
    this.conversation.push(entry)
    this.streamingEntryId = entry.id
  }

  private finalizeStreamingEntry(): void {
    if (!this.streamingEntryId) return
    this.finalizeMarkdown(this.streamingEntryId)
    const index = this.conversation.findIndex((entry) => entry.id === this.streamingEntryId)
    if (index >= 0) {
      const existing = this.conversation[index]
      if (existing.phase !== "final") {
        this.conversation[index] = { ...existing, phase: "final" }
      }
    }
    this.streamingEntryId = null
  }

  private nextConversationId(): string {
    this.conversationSequence += 1
    return `conv-${this.conversationSequence}`
  }

  private addTool(kind: ToolLogKind, text: string, status?: LiveSlotStatus): void {
    const entry: ToolLogEntry = {
      id: createSlotId(),
      kind,
      text,
      status,
      createdAt: Date.now(),
    }
    this.toolEvents.push(entry)
    if (this.toolEvents.length > MAX_TOOL_HISTORY) {
      this.toolEvents.splice(0, this.toolEvents.length - MAX_TOOL_HISTORY)
    }
  }

  private formatToolSlot(payload: unknown): { text: string; color?: string; summary?: string } {
    const data = isRecord(payload) ? payload : {}
    const toolName = extractString(data, ["tool", "name", "command"]) ?? "Tool"
    const action = extractString(data, ["action", "method", "kind"]) ?? "running"
    const progress = extractProgress(data)
    const progressText = progress != null ? ` (${Math.round(progress)}%)` : ""
    return { text: `${toolName}: ${action}${progressText}`, color: "#FACC15", summary: this.extractDiffSummary(payload) }
  }

  private extractDiffSummary(payload: unknown): string | undefined {
    if (!isRecord(payload)) return undefined
    const diffPreview = payload.diff_preview
    if (typeof diffPreview === "string") return diffPreview
    if (isRecord(diffPreview)) {
      const additions = typeof diffPreview.additions === "number" ? diffPreview.additions : undefined
      const deletions = typeof diffPreview.deletions === "number" ? diffPreview.deletions : undefined
      const files = Array.isArray(diffPreview.files) ? diffPreview.files.slice(0, 3).join(", ") : undefined
      if (additions != null || deletions != null || files) {
        const parts: string[] = []
        if (additions != null || deletions != null) parts.push(`Δ +${additions ?? 0}/-${deletions ?? 0}`)
        if (files) parts.push(`in ${files}`)
        return parts.join(" ")
      }
    }
    const text =
      typeof payload.result === "string"
        ? payload.result
        : typeof payload.output === "string"
          ? payload.output
          : typeof payload.diff === "string"
            ? payload.diff
            : undefined
    if (text) {
      const preview = computeDiffPreview(text.split(/\r?\n/))
      if (preview) {
        const files = preview.files.length > 0 ? ` in ${preview.files.join(", ")}` : ""
        return `Δ +${preview.additions}/-${preview.deletions}${files}`
      }
    }
    return undefined
  }

  private shouldStreamMarkdown(): boolean {
    return this.viewPrefs.richMarkdown && !this.markdownGloballyDisabled
  }

  private ensureMarkdownStreamer(entryId: string): { streamer: MarkdownStreamer; lastText: string } | null {
    if (!this.shouldStreamMarkdown()) return null
    const existing = this.markdownStreams.get(entryId)
    if (existing) return existing
    const streamer = new MarkdownStreamer()
    streamer.subscribe((blocks, meta) => this.applyMarkdownBlocks(entryId, blocks, meta?.error ?? null, meta?.finalized ?? false))
    streamer.initialize()
    const state = { streamer, lastText: "" }
    this.markdownStreams.set(entryId, state)
    return state
  }

  private appendMarkdownChunk(text: string): void {
    if (!this.streamingEntryId || !this.shouldStreamMarkdown()) return
    const entryId = this.streamingEntryId
    const state = this.ensureMarkdownStreamer(entryId)
    if (!state) return
    const previous = state.lastText
    const delta = text.startsWith(previous) ? text.slice(previous.length) : text
    state.lastText = previous + delta
    state.streamer.append(delta)
    this.markEntryMarkdownStreaming(entryId, true)
  }

  private finalizeMarkdown(entryId: string | null): void {
    if (!entryId) return
    const state = this.markdownStreams.get(entryId)
    if (!state) return
    state.streamer.finalize()
    const blocks = [...state.streamer.getBlocks()]
    const error = state.streamer.getError()
    state.streamer.dispose()
    this.markdownStreams.delete(entryId)
    this.applyMarkdownBlocks(entryId, blocks, error ?? null, true)
  }

  private disposeAllMarkdown(): void {
    for (const [entryId, state] of this.markdownStreams.entries()) {
      const blocks = [...state.streamer.getBlocks()]
      const error = state.streamer.getError()
      state.streamer.dispose()
      this.applyMarkdownBlocks(entryId, blocks, error ?? null, true)
    }
    this.markdownStreams.clear()
  }

  private applyMarkdownBlocks(entryId: string, blocks: ReadonlyArray<Block>, error: string | null, finalized: boolean): void {
    const index = this.conversation.findIndex((entry) => entry.id === entryId)
    if (index === -1) return
    const existing = this.conversation[index]
    const next: ConversationEntry = {
      ...existing,
      richBlocks: [...blocks],
      markdownError: error,
      markdownStreaming: existing.phase === "streaming" && !finalized,
    }
    this.conversation[index] = next
    if (error) {
      this.markdownGloballyDisabled = true
      this.pushHint(`Rich markdown disabled: ${error}`)
    }
    if (!this.eventsScheduled) this.emitChange()
  }

  private markEntryMarkdownStreaming(entryId: string, streaming: boolean): void {
    const index = this.conversation.findIndex((entry) => entry.id === entryId)
    if (index === -1) return
    const existing = this.conversation[index]
    if (existing.markdownStreaming === streaming) return
    this.conversation[index] = { ...existing, markdownStreaming: streaming }
  }

  private upsertLiveSlot(
    id: string,
    text: string,
    color?: string,
    status: LiveSlotStatus = "pending",
    stickyMs?: number,
    summary?: string,
  ): void {
    this.clearLiveSlotTimer(id)
    const existing = this.liveSlots.get(id)
    if (
      existing &&
      existing.text === text &&
      existing.color === color &&
      existing.status === status &&
      existing.summary === summary
    )
      return
    const entry: LiveSlotEntry = { id, text, color, status, updatedAt: Date.now(), summary }
    this.liveSlots.set(id, entry)
    if (stickyMs && stickyMs > 0) {
      const timer = setTimeout(() => {
        const current = this.liveSlots.get(id)
        if (current && current.updatedAt === entry.updatedAt) {
          this.removeLiveSlot(id)
        }
      }, stickyMs)
      this.liveSlotTimers.set(id, timer)
    }
    if (!this.eventsScheduled) this.emitChange()
  }

  private finalizeLiveSlot(
    id: string,
    status: LiveSlotStatus,
    fallbackText?: string,
    fallbackColor?: string,
    summary?: string,
  ): void {
    const existing = this.liveSlots.get(id)
    const text = fallbackText ?? existing?.text ?? "Tool completed"
    const color = fallbackColor ?? existing?.color
    this.upsertLiveSlot(id, text, color, status, 1200, summary ?? existing?.summary)
  }

  private clearLiveSlotTimer(id: string): void {
    const timer = this.liveSlotTimers.get(id)
    if (timer) {
      clearTimeout(timer)
      this.liveSlotTimers.delete(id)
    }
  }

  private removeLiveSlot(id: string): void {
    this.clearLiveSlotTimer(id)
    if (this.liveSlots.delete(id) && !this.eventsScheduled) {
      this.emitChange()
    }
  }

  private setGuardrailNotice(summary: string, detail?: string): void {
    this.guardrailNotice = {
      id: `guard-${Date.now().toString(36)}`,
      summary,
      detail,
      timestamp: Date.now(),
      expanded: false,
    }
    if (!this.eventsScheduled) this.emitChange()
  }

  private clearGuardrailNotice(): void {
    if (this.guardrailNotice) {
      this.guardrailNotice = null
      if (!this.eventsScheduled) this.emitChange()
    }
  }

  toggleGuardrailNotice(): void {
    if (!this.guardrailNotice) return
    this.guardrailNotice = { ...this.guardrailNotice, expanded: !this.guardrailNotice.expanded }
    this.addTool("status", `[guardrail] ${this.guardrailNotice.expanded ? "expanded" : "collapsed"}`, "error")
    this.emitChange()
  }

  dismissGuardrailNotice(): void {
    if (this.guardrailNotice) {
      this.addTool("status", "[guardrail] dismissed", "error")
    }
    this.clearGuardrailNotice()
  }

  private updateViewPrefs(update: Partial<TranscriptPreferences>, message?: string): void {
    this.viewPrefs = { ...this.viewPrefs, ...update }
    if (message) {
      this.pushHint(message)
    } else {
      this.emitChange()
    }
  }

  private isToolResultError(payload: unknown): boolean {
    const data = isRecord(payload) ? payload : {}
    if (data.error) return true
    if (typeof data.status === "string" && data.status.toLowerCase().includes("error")) return true
    const message = isRecord(data.message) ? data.message : undefined
    const messageStatus = typeof message?.status === "string" ? message.status.toLowerCase() : undefined
    if (messageStatus && messageStatus.includes("error")) return true
    const resultText = typeof message?.content === "string" ? message.content.toLowerCase() : ""
    if (resultText.includes("error") || resultText.includes("failed")) return true
    return false
  }

  private pushHint(msg: string): void {
    this.hints.push(msg)
    if (this.hints.length > MAX_HINTS) this.hints.shift()
    this.emitChange()
  }

  private emitChange(): void {
    if (this.emitScheduled) return
    this.emitScheduled = true
    queueMicrotask(() => {
      this.emitScheduled = false
      this.emit("change", this.getState())
    })
  }

  private async streamLoop(): Promise<void> {
    const appConfig = this.providers.args.config
    while (!this.abortRequested) {
      this.abortController = new AbortController()
      try {
        for await (const event of this.providers.sdk.stream(this.sessionId, { signal: this.abortController.signal })) {
          this.consecutiveFailures = 0
          this.stats.eventCount += 1
          this.stats.lastTurn = event.turn ?? this.stats.lastTurn
          this.enqueueEvent(event)
        }
        if (!this.awaitingRestart) {
          this.pendingResponse = false
          this.consecutiveFailures += 1
          if (this.consecutiveFailures > MAX_RETRIES) {
            this.pushHint("Stream ended unexpectedly and reconnection attempts exhausted.")
            this.status = "Disconnected"
            this.disconnected = true
            this.emitChange()
            break
          }
          const retryDelay = Math.min(4000, 500 * 2 ** (this.consecutiveFailures - 1))
          this.pushHint(`Stream ended unexpectedly. Retrying in ${retryDelay}ms (attempt ${this.consecutiveFailures}/${MAX_RETRIES}).`)
          this.status = `Reconnecting (${this.consecutiveFailures}/${MAX_RETRIES})`
          this.emitChange()
          await sleep(retryDelay)
          continue
        }
        this.awaitingRestart = false
        this.consecutiveFailures = 0
        this.status = "Restarting…"
        this.pendingResponse = true
        this.emitChange()
        await sleep(250)
      } catch (error) {
        if (this.abortController.signal.aborted) {
          if (this.abortRequested) {
            this.pendingResponse = false
            this.status = "Aborted"
            this.disconnected = true
            this.emitChange()
            break
          }
          if (this.awaitingRestart) {
            this.awaitingRestart = false
            this.consecutiveFailures = 0
            this.pendingResponse = true
            this.status = "Restarting…"
            this.emitChange()
            await sleep(250)
            continue
          }
        }
        this.consecutiveFailures += 1
        const delay = Math.min(4000, 500 * 2 ** (this.consecutiveFailures - 1))
        const message = error instanceof Error ? error.message : String(error)
        if (this.consecutiveFailures > MAX_RETRIES) {
          this.pendingResponse = false
          this.pushHint(`Stream interruption: ${message}. Giving up after ${MAX_RETRIES} attempts.`)
          this.status = "Disconnected"
          this.disconnected = true
          this.emitChange()
          break
        }
        this.pendingResponse = false
        this.pushHint(`Stream interruption: ${message}. Retrying in ${delay}ms (attempt ${this.consecutiveFailures}/${MAX_RETRIES}).`)
        this.status = `Reconnecting (${this.consecutiveFailures}/${MAX_RETRIES})`
        this.emitChange()
        await sleep(delay)
      }
    }
  }

  private enqueueEvent(event: SessionEvent): void {
    this.pendingEvents.push(event)
    if (this.eventsScheduled) return
    this.eventsScheduled = true
    queueMicrotask(() => {
      this.eventsScheduled = false
      const queue = this.pendingEvents
      this.pendingEvents = []
      for (const item of queue) {
        this.applyEvent(item)
      }
      this.emitChange()
    })
  }

  private applyEvent(event: SessionEvent): void {
    switch (event.type) {
      case "assistant_message": {
        if (this.pendingResponse) this.status = "Assistant responding…"
        const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
        const normalizedText = this.normalizeAssistantText(text)
        this.addConversation("assistant", normalizedText, "streaming")
        this.appendMarkdownChunk(normalizedText)
        this.pendingResponse = false
        break
      }
      case "user_message": {
        const text = typeof event.payload?.text === "string" ? event.payload.text : JSON.stringify(event.payload)
        this.addConversation("user", text)
        break
      }
      case "permission_request": {
        this.finalizeStreamingEntry()
        const payload = isRecord(event.payload) ? event.payload : {}
        const requestId =
          typeof payload.request_id === "string"
            ? payload.request_id
            : typeof payload.id === "string"
              ? payload.id
              : event.id
        const tool = extractString(payload, ["tool", "tool_name", "name"]) ?? "Tool"
        const kind = extractString(payload, ["kind", "category", "type"]) ?? tool
        const rewindable = payload.rewindable === false ? false : true
        const summary =
          extractString(payload, ["summary", "message", "prompt"]) ??
          `Permission required for ${tool}.`
        const diffText =
          typeof payload.diff === "string"
            ? payload.diff
            : typeof payload.diff_text === "string"
              ? payload.diff_text
              : null
        const ruleSuggestion =
          typeof payload.rule === "string"
            ? payload.rule
            : typeof payload.rule_suggestion === "string"
              ? payload.rule_suggestion
              : null
        const defaultScope = this.normalizeScope(payload.default_scope)
        const request: PermissionRequest = {
          requestId,
          tool,
          kind,
          rewindable,
          summary,
          diffText,
          ruleSuggestion,
          defaultScope,
          createdAt: Date.now(),
        }
        if (this.permissionActive) {
          this.permissionQueue.push(request)
        } else {
          this.permissionActive = request
        }
        this.pendingResponse = false
        this.status = "Permission required"
        this.pushHint(`Permission needed: ${tool}.`)
        this.addTool("status", `[permission] ${tool} (${kind})`, "pending")
        break
      }
      case "checkpoint_list": {
        const payload = isRecord(event.payload) ? event.payload : {}
        const rawList = Array.isArray(payload.checkpoints)
          ? payload.checkpoints
          : Array.isArray(payload.items)
            ? payload.items
            : Array.isArray(event.payload)
              ? (event.payload as unknown[])
              : []
        const parsed: CheckpointSummary[] = []
        for (const entry of rawList) {
          const summary = this.parseCheckpointSummary(entry)
          if (summary) parsed.push(summary)
        }
        parsed.sort((a, b) => b.createdAt - a.createdAt)
        this.rewindMenu = { status: "ready", checkpoints: parsed }
        this.status = "Checkpoints ready"
        this.pushHint(`Loaded ${parsed.length} checkpoint${parsed.length === 1 ? "" : "s"}.`)
        break
      }
      case "checkpoint_restored": {
        const payload = isRecord(event.payload) ? event.payload : {}
        const checkpointId = extractString(payload, ["checkpoint_id", "id"]) ?? null
        const mode = extractString(payload, ["mode"]) ?? null
        const prune = typeof payload.prune === "boolean" ? payload.prune : mode !== "code"
        if (checkpointId && prune) {
          const checkpoints =
            this.rewindMenu.status === "ready" || this.rewindMenu.status === "error" || this.rewindMenu.status === "loading"
              ? this.rewindMenu.checkpoints
              : []
          const index = checkpoints.findIndex((entry) => entry.checkpointId === checkpointId)
          if (index >= 0) {
            const next = checkpoints.slice(index)
            this.rewindMenu = { status: "ready", checkpoints: next }
          }
        }
        this.closeRewindMenu()
        this.status = "Rewind applied"
        this.pushHint(mode ? `Rewind restored (${mode}).` : "Rewind restored.")
        break
      }
      case "tool_call": {
        if (this.pendingResponse) this.status = "Tool call in progress…"
        this.stats.toolCount += 1
        const payloadText = JSON.stringify(event.payload)
        this.addTool("call", `[call] ${payloadText}`, "pending")
        const slotId = createSlotId()
        const callKey = typeof event.payload?.call_id === "string" ? event.payload.call_id : slotId
        const slot = this.formatToolSlot(event.payload)
        if (typeof event.payload?.call_id === "string") {
          this.toolSlotsByCallId.set(callKey, slotId)
        } else {
          this.toolSlotFallback.push(slotId)
        }
        this.upsertLiveSlot(slotId, slot.text, slot.color, "pending")
        break
      }
      case "tool_result": {
        if (this.pendingResponse) this.status = "Tool result received"
        this.stats.toolCount += 1
        const resultWasError = this.isToolResultError(event.payload)
        this.addTool("result", `[result] ${JSON.stringify(event.payload)}`, resultWasError ? "error" : "success")
        const callKey = typeof event.payload?.call_id === "string" ? event.payload.call_id : undefined
        if (callKey) {
          const slotId = this.toolSlotsByCallId.get(callKey)
          if (slotId) {
            this.toolSlotsByCallId.delete(callKey)
            this.finalizeLiveSlot(slotId, resultWasError ? "error" : "success")
          }
        } else {
          const slotId = this.toolSlotFallback.pop()
          if (slotId) this.finalizeLiveSlot(slotId, resultWasError ? "error" : "success")
        }
        break
      }
      case "reward_update": {
        if (this.pendingResponse) this.status = "Reward update received"
        const summary = JSON.stringify(event.payload.summary ?? event.payload)
        this.addTool("reward", `[reward] ${summary}`, "success")
        this.upsertLiveSlot("reward", `Reward update: ${summary}`, "#38bdf8", "success", 2000)
        break
      }
      case "error": {
        const message = JSON.stringify(event.payload)
        this.pushHint(`[error] ${message}`)
        this.addTool("error", `[error] ${message}`, "error")
        this.pendingResponse = false
        this.status = "Error received"
        break
      }
      case "completion": {
        this.finalizeStreamingEntry()
        const view = formatCompletion(event.payload)
        if (DEBUG_EVENTS) {
          console.log(
            `[repl event] completion`,
            JSON.stringify({
              session: this.sessionId,
              completed: view.completed,
              hints: view.hint,
            }),
          )
        }
        this.completionReached = view.completed
        this.completionSeen = true
        this.lastCompletion = {
          completed: view.completed,
          summary: (event.payload && (event.payload.summary as Record<string, unknown> | undefined)) ?? null,
        }
        this.pendingResponse = false
        this.status = view.status
        this.addTool("completion", `[completion] ${view.toolLine}`, view.completed ? "success" : "error")
        if (view.conversationLine) {
          const lastEntry = this.conversation.length > 0 ? this.conversation[this.conversation.length - 1] : undefined
          if (!(lastEntry && lastEntry.speaker === "system" && lastEntry.text === view.conversationLine)) {
            this.addConversation("system", view.conversationLine)
          }
        }
        if (view.hint) this.pushHint(view.hint)
        if (view.warningSlot) {
          this.upsertLiveSlot("guardrail", view.warningSlot.text, view.warningSlot.color, "error")
          this.setGuardrailNotice(view.warningSlot.text, view.hint ?? view.conversationLine)
        } else {
          this.removeLiveSlot("guardrail")
          this.clearGuardrailNotice()
        }
        this.toolSlotsByCallId.forEach((slotId) => this.removeLiveSlot(slotId))
        this.toolSlotsByCallId.clear()
        this.toolSlotFallback.splice(0).forEach((slotId) => this.removeLiveSlot(slotId))
        this.removeLiveSlot("reward")
        break
      }
      case "run_finished": {
        this.finalizeStreamingEntry()
        if (typeof event.payload?.eventCount === "number" && Number.isFinite(event.payload.eventCount)) {
          this.stats.eventCount = event.payload.eventCount
        }
        const completed = Boolean(event.payload?.completed)
        const reason = typeof event.payload?.reason === "string" ? event.payload.reason : undefined
        this.pendingResponse = false
        this.status = completed ? "Finished" : "Halted"
        if (!this.completionSeen) {
          this.completionSeen = true
          this.completionReached = completed
        }
        const hint = reason ? `Run finished (${reason}).` : "Run finished."
        this.pushHint(hint)
        break
      }
      default:
        break
    }
  }

  private normalizeAssistantText(text: string): string {
    const trimmed = text.trim()
    if (trimmed.startsWith("<TOOL_CALL>") && trimmed.includes("mark_task_complete")) {
      return "No coding task detected. Describe a concrete change (e.g., \"Implement bubble sort in sorter.py\") or switch models with /model."
    }
    return text
  }
}
