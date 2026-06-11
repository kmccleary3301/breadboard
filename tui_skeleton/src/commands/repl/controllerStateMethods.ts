import { ApiError } from "../../api/client.js"
import type { SessionEvent } from "../../api/types.js"
import type { SessionFileInfo } from "../../api/types.js"
import { BRAND_COLORS, resolveIcons } from "../../repl/designSystem.js"
import { SLASH_COMMANDS } from "../../repl/slashCommands.js"
import { computeDiffPreview } from "../../repl/transcriptUtils.js"
import {
  materializeToolArtifactRef,
  normalizeArtifactRef,
  shouldMaterializeToolArtifact,
} from "./toolArtifactRef.js"
import { clearSessionReplayState } from "./controllerUserMethods.js"
import { isLifecycleNoiseHint } from "./hintPolicy.js"
import type {
  CheckpointSummary,
  ConversationEntry,
  CTreeSnapshot,
  LiveSlotStatus,
  PermissionDecision,
  PermissionRequest,
  PermissionRuleScope,
  TaskEntry,
  TodoItem,
  ToolDisplayPayload,
  ToolLogEntry,
  ToolLogKind,
} from "../../repl/types.js"
import {
  MAX_HINTS,
  MAX_RAW_EVENTS,
  MAX_RAW_EVENT_CHARS,
  MAX_TOOL_EXEC_OUTPUT,
  MAX_TOOL_HISTORY,
  createSlotId,
  extractProgress,
  extractString,
  extractUsageMetrics,
  isRecord,
  numberOrUndefined,
  parseTodoEntry,
  parseTodoList,
  tryParseJsonTodos,
} from "./controllerUtils.js"
import { formatActivityTransitionTimeline } from "./controllerTransitionTimeline.js"
import { getActiveEngineLifecycleSnapshot, restartOwnedEngine } from "../../engine/engineSupervisor.js"
import { assertDurableTranscriptSafe } from "./transcriptSafety.js"

type SlashHandler = (args: string[]) => Promise<void>

const normalizeTaskStatus = (rawStatus?: string | null, rawKind?: string | null): string | null => {
  const seed = (rawStatus ?? rawKind ?? "").toLowerCase()
  if (!seed) return rawStatus ?? rawKind ?? null
  if (
    seed.includes("complete") ||
    seed.includes("done") ||
    seed.includes("success") ||
    seed === "end" ||
    seed === "ended" ||
    seed === "finished"
  ) {
    return "completed"
  }
  if (seed.includes("error") || seed.includes("fail")) return "failed"
  if (seed.includes("cancel") || seed.includes("stop")) return "stopped"
  if (seed.includes("start") || seed.includes("run") || seed.includes("progress")) return "running"
  return rawStatus ?? rawKind ?? null
}

const clampLines = (text: string, maxLines: number): string => {
  const lines = text.split(/\r?\n/)
  if (lines.length <= maxLines) return text
  return `${lines.slice(0, maxLines).join("\n")}…`
}

const clampChars = (text: string, maxChars: number): string =>
  text.length <= maxChars ? text : `${text.slice(0, Math.max(0, maxChars - 1))}…`

const formatThinkingPreview = (raw: string, maxLines: number, maxChars: number): string =>
  clampChars(clampLines(raw, maxLines), maxChars)

const resolveTranscriptTimestamp = (ctx: any): number => {
  const seq = ctx.currentEventSeq
  if (typeof seq === "number" && Number.isFinite(seq)) return seq
  ctx.eventClock = typeof ctx.eventClock === "number" && Number.isFinite(ctx.eventClock) ? ctx.eventClock + 1 : 1
  return ctx.eventClock
}

export function slashHandlers(this: any): Record<string, SlashHandler> {
  return {
    quit: async () => {
      this.pushHint("Exiting session…")
      this.status = "Exiting…"
      await this.stop()
    },
    stop: async () => {
      if (this.disconnected) {
        this.pendingResponse = false
        this.awaitingRestart = false
        this.sessionRestarting = false
        this.addTool(
          "status",
          "[stop]\nrecovery=stopped\nnext=/retry to resubmit, /resume to inspect sessions, /new to start over, or /engine restart for owned engines",
          "warning",
        )
        this.pushHint("Recovery stopped. The prior submitted prompt remains preserved locally; use /retry to resubmit if needed.")
        this.setActivityStatus("Recovery stopped (session missing)", {
          to: "halted",
          eventType: "recovery.stop",
          source: "user",
        })
        this.status = "Recovery stopped (session missing)"
        this.emitChange()
        return
      }
      const ok = await this.runSessionCommand("stop", undefined, "Interrupt requested.")
      if (ok) {
        this.pendingResponse = false
        this.status = "Stopping…"
        this.noteStopRequested()
      }
    },
    help: async () => {
      const summary = SLASH_COMMANDS.map((entry) => `/${entry.name}${entry.usage ? ` ${entry.usage}` : ""} — ${entry.summary}`).join(" | ")
      this.pushHint(summary)
    },
    clear: async () => {
      this.viewClearAt = Date.now()
      this.hints.length = 0
      this.pushHint("Cleared view (history preserved).")
    },
    new: async () => {
      const previousSessionId = this.sessionId
      this.pushHint("Starting new session…")
      this.status = "Starting new session…"
      this.emitChange()
      this.sessionRestarting = true
      try {
        await this.stop()
        clearSessionReplayState(this)
        this.sessionId = ""
        await this.start()
        this.status = "Ready"
        this.pushHint(`Started new session ${this.sessionId}.`)
      } catch (error) {
        this.status = previousSessionId ? `Still on ${previousSessionId}` : "New session failed"
        this.pushHint(`Failed to start new session: ${(error as Error).message}`)
        this.emitChange()
      } finally {
        this.sessionRestarting = false
      }
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
    "todo-scope": async (args) => {
      const normalizeKey = (value: string): string => value.trim().replace(/\s+/g, "_").slice(0, 64)
      const scopeOrder: string[] = Array.isArray(this.todoScopeOrder) && this.todoScopeOrder.length > 0 ? [...this.todoScopeOrder] : ["main"]
      const stores: Record<string, any> = this.todoStoresByScope && typeof this.todoStoresByScope === "object" ? this.todoStoresByScope : {}
      const labels: Record<string, string> =
        this.todoScopeLabelsByKey && typeof this.todoScopeLabelsByKey === "object" ? this.todoScopeLabelsByKey : {}
      const stale: Record<string, boolean> =
        this.todoScopeStaleByKey && typeof this.todoScopeStaleByKey === "object" ? this.todoScopeStaleByKey : {}

      const current = typeof this.activeTodoScopeKey === "string" && this.activeTodoScopeKey.trim() ? this.activeTodoScopeKey : "main"
      const action = (args[0] ?? "status").toLowerCase()

      const describe = (key: string): string => {
        const label = labels[key] ?? key
        const count = Array.isArray(stores[key]?.order) ? stores[key].order.length : 0
        const activeMark = key === current ? "*" : " "
        const staleMark = stale[key] ? " (stale)" : ""
        return `${activeMark} ${key}${label && label !== key ? ` (${label})` : ""} · ${count} item${count === 1 ? "" : "s"}${staleMark}`
      }

      const setActive = (key: string): void => {
        const next = normalizeKey(key)
        if (!next) return
        this.activeTodoScopeKey = next
        if (this.todoScopeLabelsByKey && !this.todoScopeLabelsByKey[next]) {
          this.todoScopeLabelsByKey[next] = next
        }
        this.noteTodoScopeManualSelection?.()
        this.emitChange()
      }

      if (action === "status" || action === "show") {
        const label = labels[current] ?? current
        const count = Array.isArray(stores[current]?.order) ? stores[current].order.length : 0
        const staleMark = stale[current] ? " (stale)" : ""
        this.pushHint(`Todo scope: ${current}${label && label !== current ? ` (${label})` : ""} · ${count} item${count === 1 ? "" : "s"}${staleMark}`)
        if (scopeOrder.length > 1) {
          this.pushHint(`Scopes: ${scopeOrder.map((key) => (key === current ? `*${key}` : key)).join(", ")} (Ctrl+U to cycle)`)
        }
        return
      }
      if (action === "list") {
        const lines = scopeOrder.map(describe)
        this.addTool("status", `[todo-scope] ${lines.join(" | ")}`)
        this.pushHint(lines.join(" | "))
        return
      }
      if (action === "next" || action === "prev") {
        const dir = action === "prev" ? -1 : 1
        const idx = Math.max(0, scopeOrder.indexOf(current))
        const nextIdx = scopeOrder.length > 0 ? (idx + dir + scopeOrder.length) % scopeOrder.length : 0
        const nextKey = scopeOrder[nextIdx] ?? "main"
        setActive(nextKey)
        this.pushHint(`Todo scope: ${describe(nextKey).replace(/^\\*\\s+/, "")}`)
        return
      }
      if (action === "set") {
        const key = args[1] ? normalizeKey(args[1]) : ""
        if (!key) {
          this.pushHint("Usage: /todo-scope set <key>")
          return
        }
        if (!stores[key]) {
          this.pushHint(`Unknown todo scope: ${key}`)
          return
        }
        setActive(key)
        this.pushHint(`Todo scope: ${describe(key).replace(/^\\*\\s+/, "")}`)
        return
      }
      this.pushHint("Usage: /todo-scope [status|list|next|prev|set <key>]")
    },
    ctree: async (args) => {
      const action = args[0]?.toLowerCase()
      if (!action || action === "status") {
        const parts = [
          `stage ${this.ctreeStage}`,
          `previews ${this.ctreeIncludePreviews ? "on" : "off"}`,
          `source ${this.ctreeSource}`,
          this.ctreeTreeStatus === "loading" ? "loading" : undefined,
        ].filter(Boolean)
        this.pushHint(`CTree: ${parts.join(" · ")}`)
        if (!this.ctreeTree) {
          await this.requestCtreeTree()
        }
        return
      }
      if (action === "refresh" || action === "reload") {
        await this.refreshCtreeTree()
        return
      }
      if (action === "stage") {
        const stage = args[1]?.toUpperCase()
        if (!stage || !["RAW", "SPEC", "HEADER", "FROZEN"].includes(stage)) {
          this.pushHint("Usage: /ctree stage <raw|spec|header|frozen>.")
          return
        }
        await this.setCtreeStage(stage)
        return
      }
      if (action === "previews" || action === "preview") {
        const value = args[1]?.toLowerCase()
        if (value && !["on", "off"].includes(value)) {
          this.pushHint("Usage: /ctree previews <on|off>.")
          return
        }
        const next = value ? value === "on" : !this.ctreeIncludePreviews
        await this.setCtreePreviews(next)
        return
      }
      if (action === "source") {
        const source = args[1]?.toLowerCase()
        if (!source || !["auto", "disk", "eventlog", "memory"].includes(source)) {
          this.pushHint("Usage: /ctree source <auto|disk|eventlog|memory>.")
          return
        }
        await this.setCtreeSource(source)
        return
      }
      this.pushHint("Usage: /ctree [status|refresh|stage|previews|source] …")
    },
    inspect: async (args) => {
      const action = args[0]?.toLowerCase()
      if (action === "close") {
        this.closeInspectMenu()
        return
      }
      if (action === "refresh" || action === "reload") {
        await this.refreshInspectMenu()
        return
      }
      if (this.inspectMenu.status === "hidden") {
        await this.openInspectMenu()
      } else {
        this.closeInspectMenu()
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
      const lifecycle = getActiveEngineLifecycleSnapshot() ?? this.lifecycleSnapshot ?? null
      if (this.disconnected && lifecycle?.mode === "local-owned") {
        const recovered = await this.recoverIdleSessionAfterEngineRestart?.({ preserveLocalTranscript: true })
        if (!recovered) {
          this.pushHint("Retry blocked: could not recover a new owned-engine session.")
          return
        }
        this.streamTask = this.streamLoop()
      }
      this.addConversation("user", payload.content)
      try {
        await this.dispatchSubmission(payload, "Retrying prior input…")
      } catch {
        // handled in dispatchSubmission
      }
    },
    resume: async () => {
      if (this.pendingResponse) {
        this.pushHint("Already waiting for a response. Use /resume after the current turn completes or /stop first.")
        return
      }
      const lifecycle = getActiveEngineLifecycleSnapshot() ?? this.lifecycleSnapshot ?? null
      if (!this.disconnected) {
        this.addTool("status", `[resume]\nresume=already-active session=${this.sessionId ?? "unknown"}`, "success")
        this.pushHint("Session is already active. Use /sessions to inspect recent sessions.")
        return
      }
      if (lifecycle?.mode !== "local-owned") {
        this.addTool(
          "status",
          `[resume]\nresume=unavailable mode=${lifecycle?.mode ?? "unknown"}\nnext=restart or reconnect the external engine, then use /sessions`,
          "error",
        )
        this.pushHint("Resume recovery is only automatic in local-owned mode.")
        return
      }
      const recovered = await this.recoverIdleSessionAfterEngineRestart?.({ preserveLocalTranscript: true })
      if (!recovered) {
        this.addTool("status", "[resume]\nresume=failed\nnext=/engine restart or /new", "error")
        this.pushHint("Resume failed; use /engine restart or /new.")
        return
      }
      if (!this.streamTask) {
        this.streamTask = this.streamLoop()
      }
      this.addTool(
        "status",
        `[resume]\nresume=recovered-new-session session=${this.sessionId ?? "unknown"}\ncontext=reset localTranscript=preserved`,
        "success",
      )
      this.pushHint("Resume recovered a new owned-engine session; local transcript is preserved, engine context was reset.")
    },
    follow: async (args) => {
      const action = (args[0] ?? "status").toLowerCase()
      if (!["status", "pause", "off", "resume", "on", "live"].includes(action)) {
        this.pushHint("Usage: /follow [status|pause|resume].")
        return
      }
      if (action === "status") {
        if (!this.pendingResponse) {
          this.pushHint("No active run. Default behavior is live follow for the next run.")
          return
        }
        this.pushHint(`Follow is ${this.mainFollowTail ? "live" : "paused"} for the current run.`)
        return
      }
      if (["resume", "on", "live"].includes(action)) {
        if (!this.pendingResponse) {
          this.mainFollowTail = true
          this.pushHint("No active run. Default behavior remains live follow.")
          return
        }
        if (this.mainFollowTail) {
          this.pushHint("Follow is already live for the current run.")
          return
        }
        this.mainFollowTail = true
        this.pushHint("Follow resumed for the current run.")
        return
      }
      if (!this.pendingResponse) {
        this.pushHint("No active run to pause. Use /follow pause while a response is streaming.")
        return
      }
      if (!this.mainFollowTail) {
        this.pushHint("Follow is already paused for the current run.")
        return
      }
      this.mainFollowTail = false
      this.pushHint("Follow paused for the current run. Use /follow resume to catch up.")
    },
    plan: async () => {
      const ok = await this.runSessionCommand("set_mode", { mode: "plan" }, "Requested plan-focused mode.")
      if (ok) {
        this.status = "Mode request: plan"
        this.mode = "plan"
      }
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
      if (ok) {
        this.status = `Mode request: ${normalized}`
        this.mode = normalized
      }
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
          `View prefs — collapse: ${this.viewPrefs.collapseMode}, scroll: ${this.viewPrefs.virtualization}, markdown: ${this.viewPrefs.richMarkdown ? "on" : "off"}, raw: ${this.viewPrefs.rawStream ? "on" : "off"}, tools: ${this.viewPrefs.toolRail ? "rail" : "off"}${this.viewPrefs.toolInline ? "+inline" : ""}, reasoning: ${this.viewPrefs.showReasoning ? "on" : "off"}. Usage: /view collapse <auto|all|none>, /view scroll <auto|compact>, /view markdown <on|off>, /view raw <on|off>, /view tools <rail|inline|both|off>, /view reasoning <on|off>.`,
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
      if (scope === "raw" || scope === "stream") {
        const value = args[1]?.toLowerCase()
        if (!value || !["on", "off"].includes(value)) {
          this.pushHint(`Usage: /view raw <on|off>. Currently ${this.viewPrefs.rawStream ? "on" : "off"}.`)
          return
        }
        const enabled = value === "on"
        this.updateViewPrefs({ rawStream: enabled }, `Raw stream ${enabled ? "enabled" : "disabled"}.`)
        return
      }
      if (scope === "tools" || scope === "tool") {
        const value = args[1]?.toLowerCase()
        if (!value || !["rail", "inline", "both", "off"].includes(value)) {
          this.pushHint("Usage: /view tools <rail|inline|both|off>.")
          return
        }
        const toolRail = value === "rail" || value === "both"
        const toolInline = value === "inline" || value === "both"
        this.updateViewPrefs(
          { toolRail, toolInline },
          `Tools view set to ${value}.`,
        )
        return
      }
      if (scope === "reasoning") {
        const value = args[1]?.toLowerCase()
        if (!value || !["on", "off"].includes(value)) {
          this.pushHint(`Usage: /view reasoning <on|off>. Currently ${this.viewPrefs.showReasoning ? "on" : "off"}.`)
          return
        }
        const enabled = value === "on"
        const fullAllowed = this.runtimeFlags?.allowFullThinking === true
        if (enabled && !fullAllowed) {
          this.updateViewPrefs(
            { showReasoning: enabled },
            "Reasoning set to summary mode. Enable BREADBOARD_THINKING_FULL_OPT_IN=1 to allow full reasoning output.",
          )
          return
        }
        this.updateViewPrefs({ showReasoning: enabled }, `Reasoning stream ${enabled ? "enabled" : "disabled"}.`)
        return
      }
      this.pushHint("Usage: /view collapse <auto|all|none>, /view scroll <auto|compact>, /view markdown <on|off>, /view raw <on|off>, /view tools <rail|inline|both|off>, /view reasoning <on|off>.")
    },
    thinking: async (args) => {
      const mode = args[0]?.toLowerCase()
      if (mode && !["summary", "raw"].includes(mode)) {
        this.pushHint("Usage: /thinking [summary|raw].")
        return
      }
      const artifact = this.thinkingArtifact
      if (!artifact) {
        this.pushHint("No thinking artifact available yet.")
        return
      }
      const maxLines = Math.max(1, Number(this.runtimeFlags?.thinkingMaxLines ?? 6))
      const maxChars = Math.max(32, Number(this.runtimeFlags?.thinkingMaxChars ?? 600))
      const lines: string[] = []
      const summary = artifact.summary?.trim() ?? ""
      if (!summary && mode !== "raw") {
        this.addTool("status", "[thinking] No thinking summary available.", "success")
        this.pushHint("No thinking summary available.")
        return
      }
      lines.push(
        `[thinking] mode=${artifact.mode} finalized=${artifact.finalizedAt ? "yes" : "no"} updates=${artifact.sourceEventTypes?.length ?? 0}`,
      )
      lines.push(formatThinkingPreview(summary || "(empty summary)", maxLines, maxChars))
      if (mode === "raw") {
        const rawAllowed =
          artifact.mode === "full" &&
          this.runtimeFlags?.allowFullThinking === true &&
          this.runtimeFlags?.allowRawThinkingPeek === true
        const rawText = typeof artifact.rawText === "string" ? artifact.rawText.trim() : ""
        if (rawAllowed && rawText.length > 0) {
          lines.push("[raw]")
          lines.push(formatThinkingPreview(rawText, maxLines * 2, maxChars))
        } else {
          this.pushHint(
            "Raw thinking unavailable (requires full mode + BREADBOARD_THINKING_FULL_OPT_IN=1 + BREADBOARD_THINKING_PEEK_RAW_ALLOWED=1).",
          )
        }
      }
      this.addTool("status", lines.join("\n"), "success")
      this.pushHint("Thinking artifact shown.")
    },
    runtime: async (args) => {
      const mode = (args[0] ?? "telemetry").toLowerCase()
      if (!["telemetry", "status"].includes(mode)) {
        this.pushHint("Usage: /runtime [telemetry].")
        return
      }
      const telemetry = this.runtimeTelemetry ?? {}
      const activity = this.activity?.primary ?? "idle"
      const thinkingMode =
        this.runtimeFlags?.thinkingEnabled === false
          ? "off"
          : this.thinkingArtifact?.mode ??
            (this.viewPrefs?.showReasoning && this.runtimeFlags?.allowFullThinking === true ? "full" : "summary")
      const timeline = formatActivityTransitionTimeline(this.activityTransitionTrace ?? [], 10)
      const lines = [
        `[runtime] activity=${activity} thinking=${thinkingMode}`,
        `flags inlineThinkingBlock=${this.runtimeFlags?.inlineThinkingBlockEnabled === true ? "on" : "off"} thinkingPreview=${this.runtimeFlags?.thinkingPreviewEnabled === true ? "on" : "off"} previewLines=${this.runtimeFlags?.thinkingPreviewMaxLines ?? 0} previewTtlMs=${this.runtimeFlags?.thinkingPreviewTtlMs ?? 0} adaptiveCadence=${this.runtimeFlags?.adaptiveMarkdownCadenceEnabled === true ? "on" : "off"} eventCoalesceMs=${this.runtimeFlags?.eventCoalesceMs ?? 0} eventBatch=${this.runtimeFlags?.eventCoalesceMaxBatch ?? 0}`,
        `statusTransitions=${telemetry.statusTransitions ?? 0} suppressedTransitions=${telemetry.suppressedTransitions ?? 0} illegalTransitions=${telemetry.illegalTransitions ?? 0}`,
        `statusCommits=${telemetry.statusCommits ?? 0} statusCoalesced=${telemetry.statusCoalesced ?? 0}`,
        `markdownFlushes=${telemetry.markdownFlushes ?? 0} thinkingUpdates=${telemetry.thinkingUpdates ?? 0} thinkingPreviewOpened=${telemetry.thinkingPreviewOpened ?? 0} thinkingPreviewClosed=${telemetry.thinkingPreviewClosed ?? 0} thinkingPreviewExpired=${telemetry.thinkingPreviewExpired ?? 0} adaptiveCadenceAdjustments=${telemetry.adaptiveCadenceAdjustments ?? 0}`,
        `eventFlushes=${telemetry.eventFlushes ?? 0} eventCoalesced=${telemetry.eventCoalesced ?? 0} eventMaxQueueDepth=${telemetry.eventMaxQueueDepth ?? 0}`,
        `optimisticToolRows=${telemetry.optimisticToolRows ?? 0} optimisticToolReconciled=${telemetry.optimisticToolReconciled ?? 0} optimisticDiffRows=${telemetry.optimisticDiffRows ?? 0} optimisticDiffReconciled=${telemetry.optimisticDiffReconciled ?? 0}`,
        "[timeline]",
        timeline,
      ]
      this.addTool("status", lines.join("\n"), "success")
      this.pushHint("Runtime telemetry shown.")
    },
    engine: async (args) => {
      const action = (args[0] ?? "status").toLowerCase()
      if (!["status", "health", "logs", "log", "restart"].includes(action)) {
        this.pushHint("Usage: /engine [status|logs|restart].")
        return
      }
      const lifecycle = getActiveEngineLifecycleSnapshot() ?? this.lifecycleSnapshot ?? null
      if (action === "logs" || action === "log") {
        this.addTool("status", `[engine]\nlog=${lifecycle?.logPath ?? "unknown"}`, "success")
        this.pushHint("Engine log path shown.")
        return
      }
      if (action === "restart") {
        if (lifecycle?.mode !== "local-owned") {
          this.addTool("status", `[engine]\nrestart unavailable: lifecycle mode is ${lifecycle?.mode ?? "unknown"}.`, "error")
          this.pushHint("Engine restart is only available in local-owned mode.")
          return
        }
        if (this.pendingResponse) {
          this.addTool("status", "[engine]\nrestart unavailable while a response is unresolved. Use Esc/stop first.", "error")
          this.pushHint("Engine restart blocked while a response is unresolved.")
          return
        }
        this.sessionRestarting = true
        this.status = "Restarting engine"
        this.emitChange()
        try {
          this.abortRequested = true
          this.abortController.abort()
          if (this.streamTask) {
            await Promise.race([
              this.streamTask.catch(() => undefined),
              new Promise((resolve) => setTimeout(resolve, 750)),
            ])
          }
          this.streamTask = null
          this.abortRequested = false
          const result = await restartOwnedEngine()
          this.lifecycleRestartCount = (this.lifecycleRestartCount ?? 0) + 1
          this.lifecycleSnapshot = getActiveEngineLifecycleSnapshot() ?? this.lifecycleSnapshot
          const recovered = await this.recoverIdleSessionAfterEngineRestart?.({
            preserveLocalTranscript: this.conversation.length > 0 || this.toolEvents.length > 0,
          })
          if (recovered && !this.streamTask) {
            this.streamTask = this.streamLoop()
          }
          this.addTool(
            "status",
            [
              "[engine]",
              `restart=ok baseUrl=${result.baseUrl}`,
              `pid=${result.pid ?? "unknown"} started=${result.started ? "yes" : "no"}`,
              `sessionRecovered=${recovered ? "yes" : "no"}`,
            ].join("\n"),
            "success",
          )
          this.pushHint("Owned engine restarted.")
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          this.addTool("status", `[engine]\nrestart=failed ${message}`, "error")
          this.pushHint(`Engine restart failed: ${message}`)
        } finally {
          this.sessionRestarting = false
          this.emitChange()
        }
        return
      }
      const lines = [
        "[engine]",
        `mode=${lifecycle?.mode ?? "unknown"} source=${lifecycle?.modeSource ?? "unknown"} owned=${lifecycle?.owned === true ? "yes" : lifecycle?.owned === false ? "no" : "unknown"}`,
        `baseUrl=${lifecycle?.baseUrl ?? "unknown"}`,
        `pid=${lifecycle?.pid ?? "unknown"} restartPolicy=${lifecycle?.restartPolicy ?? "unknown"} restarts=${this.lifecycleRestartCount ?? 0}`,
        `log=${lifecycle?.logPath ?? "unknown"}`,
        `session=${this.sessionId ?? "unknown"} status=${this.status ?? "unknown"} pending=${this.pendingResponse ? "yes" : "no"} disconnected=${this.disconnected ? "yes" : "no"}`,
      ]
      try {
        const health = await this.api().health()
        lines.push(`health=${health.status ?? "ok"} protocol=${health.protocol_version ?? "unknown"} version=${health.version ?? "unknown"}`)
        if (health.served_revision?.repo_root) lines.push(`servedRoot=${health.served_revision.repo_root}`)
        if (health.served_revision?.commit) lines.push(`servedRevision=${health.served_revision.commit}${health.served_revision.dirty ? " dirty" : ""}`)
      } catch (error) {
        lines.push(`health=unavailable ${(error as Error).message}`)
      }
      this.addTool("status", lines.join("\n"), "success")
      this.pushHint("Engine status shown.")
    },
    doctor: async () => {
      const lifecycle = getActiveEngineLifecycleSnapshot() ?? this.lifecycleSnapshot ?? null
      const mode = lifecycle?.mode ?? "unknown"
      const owned = lifecycle?.owned === true
      const canRestart = mode === "local-owned" && owned
      const healthLines: string[] = []
      try {
        const health = await this.api().health()
        healthLines.push(`health=${health.status ?? "ok"} protocol=${health.protocol_version ?? "unknown"} version=${health.version ?? "unknown"}`)
        if (health.served_revision?.repo_root) healthLines.push(`servedRoot=${health.served_revision.repo_root}`)
        if (health.served_revision?.commit) healthLines.push(`servedRevision=${health.served_revision.commit}${health.served_revision.dirty ? " dirty" : ""}`)
      } catch (error) {
        healthLines.push(`health=unavailable ${(error as Error).message}`)
      }
      const recommendation = (() => {
        if (this.pendingResponse) return "recommendation=wait, /stop, or let recovery finish before manual restart"
        if (this.disconnected && canRestart) return "recommendation=/engine restart"
        if (this.disconnected) return "recommendation=restart or reconnect the external engine, then use /sessions or /new"
        if (canRestart) return "recommendation=engine is owned; /engine restart is available if recovery is needed"
        return "recommendation=engine is not owned; inspect status/logs and manage the external process separately"
      })()
      const lines = [
        "[doctor]",
        `engineMode=${mode} source=${lifecycle?.modeSource ?? "unknown"} owned=${owned ? "yes" : lifecycle?.owned === false ? "no" : "unknown"}`,
        `baseUrl=${lifecycle?.baseUrl ?? "unknown"}`,
        `pid=${lifecycle?.pid ?? "unknown"} restartPolicy=${lifecycle?.restartPolicy ?? "unknown"} restartAvailable=${canRestart ? "yes" : "no"}`,
        `session=${this.sessionId ?? "unknown"} status=${this.status ?? "unknown"} pending=${this.pendingResponse ? "yes" : "no"} disconnected=${this.disconnected ? "yes" : "no"}`,
        `log=${lifecycle?.logPath ?? "unknown"}`,
        ...healthLines,
        recommendation,
      ]
      this.addTool("status", lines.join("\n"), "success")
      this.pushHint("Doctor report shown.")
    },
    files: async (args) => {
      const scope = args[0] ?? "."
      try {
        const files = await this.api().listSessionFiles(this.sessionId, scope === "." ? undefined : scope)
        const output = files
          .map((file: SessionFileInfo) => `${file.type.padEnd(4, " ")} ${file.path}${file.size != null ? ` ${file.size}` : ""}`)
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
    skills: async () => {
      await this.openSkillsMenu()
    },
    rewind: async () => {
      await this.openRewindMenu()
    },
  }
}

export function normalizeScope(this: any, value: unknown): PermissionRuleScope {
  switch (String(value ?? "").toLowerCase()) {
    case "session":
      return "session"
    case "global":
      return "global"
    default:
      return "project"
  }
}

export function parseCheckpointSummary(this: any, entry: unknown): CheckpointSummary | null {
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

export function extractTodosFromPayload(this: any, payload: unknown): TodoItem[] | null {
  const candidates: unknown[] = []
  if (isRecord(payload)) {
    candidates.push(payload)
    if (payload.tool) candidates.push(payload.tool)
    if (payload.call) candidates.push(payload.call)
    if (payload.message) candidates.push(payload.message)
    if (payload.content) candidates.push(payload.content)
    if (payload.output) candidates.push(payload.output)
    if (payload.result) candidates.push(payload.result)
  } else {
    candidates.push(payload)
  }
  for (const candidate of candidates) {
    if (candidate == null) continue
    if (Array.isArray(candidate)) {
      const list = parseTodoList(candidate)
      if (list) return list
      continue
    }
    if (isRecord(candidate)) {
      const name =
        extractString(candidate, ["name", "tool", "tool_name", "function", "provider_name", "fn"]) ?? ""
      const normalized = name.toLowerCase()
      const likelyTodoTool = normalized.includes("todo") || normalized.includes("todowrite")
      const directTodos = parseTodoList(candidate.todos)
      if (directTodos && (likelyTodoTool || directTodos.length > 0)) {
        return directTodos
      }
      if (candidate.todo) {
        const single = parseTodoEntry(candidate.todo, "todo-1")
        if (single && (likelyTodoTool || single.title.length > 0)) {
          return [single]
        }
      }
      const nested =
        parseTodoList(candidate.output) ??
        parseTodoList(candidate.result) ??
        parseTodoList(candidate.content) ??
        tryParseJsonTodos(candidate.output) ??
        tryParseJsonTodos(candidate.result) ??
        tryParseJsonTodos(candidate.content)
      if (nested) return nested
    }
    const parsed = tryParseJsonTodos(candidate)
    if (parsed) return parsed
  }
  return null
}

export async function openRewindMenu(this: any): Promise<void> {
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

export function closeRewindMenu(this: any): void {
  if (this.rewindMenu.status !== "hidden") {
    this.rewindMenu = { status: "hidden" }
    this.emitChange()
  }
}

export async function restoreCheckpoint(this: any, checkpointId: string, mode: "conversation" | "code" | "both"): Promise<boolean> {
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

export function setPermissionActive(this: any, next: PermissionRequest | null): void {
  this.permissionActive = next
  this.permissionError = null
  if (!next) {
    if (this.permissionQueue.length > 0) {
      this.permissionActive = this.permissionQueue.shift() ?? null
    }
  }
}

export async function respondToPermission(this: any, decision: PermissionDecision): Promise<boolean> {
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
  const note = typeof decision.note === "string" ? decision.note.trim() : ""
  if (note) payload.note = note
  if (decision.kind === "deny-stop") {
    payload.stop = true
  }
  const ok = await this.runSessionCommand("permission_decision", payload, "Permission decision sent.")
  if (ok) {
    if (decision.kind === "deny-stop") {
      this.pendingResponse = false
      this.status = "Stopped (permission denied)"
      this.clearStopRequest()
      this.permissionQueue.length = 0
      this.permissionActive = null
    } else {
      this.setPermissionActive(null)
    }
    this.emitChange()
  } else {
    this.permissionError = "Permission decision failed. Check connection or engine status."
    this.emitChange()
  }
  return ok
}

export function addConversation(this: any, 
  speaker: ConversationEntry["speaker"],
  text: string,
  phase: ConversationEntry["phase"] = "final",
): void {
  if (phase === "streaming") {
    this.setStreamingConversation(speaker, text)
    return
  }
  this.finalizeStreamingEntry()
  assertDurableTranscriptSafe(text, { surface: "conversation", speaker }, `conversation:${speaker}`)
  const entry: ConversationEntry = {
    id: this.nextConversationId(),
    speaker,
    text,
    phase: "final",
    createdAt: resolveTranscriptTimestamp(this),
  }
  this.conversation.push(entry)
}

export function setStreamingConversation(this: any, speaker: ConversationEntry["speaker"], text: string): void {
  if (this.streamingEntryId) {
    const index = this.conversation.findIndex((entry: ConversationEntry) => entry.id === this.streamingEntryId)
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
    createdAt: resolveTranscriptTimestamp(this),
  }
  this.conversation.push(entry)
  this.streamingEntryId = entry.id
}

export function finalizeStreamingEntry(this: any): void {
  if (!this.streamingEntryId) return
  this.finalizeMarkdown(this.streamingEntryId)
  const index = this.conversation.findIndex((entry: ConversationEntry) => entry.id === this.streamingEntryId)
  if (index >= 0) {
    const existing = this.conversation[index]
    this.conversation[index] = {
      ...existing,
      phase: "final",
      markdownStreaming: false,
      markdownFinalized: existing.markdownFinalized === true ? true : existing.markdownFinalized,
    }
  }
  this.streamingEntryId = null
}

export function nextConversationId(this: any): string {
  this.conversationSequence += 1
  return `conv-${this.conversationSequence}`
}

export function upsertTask(this: any, entry: TaskEntry): void {
  const existing = this.taskMap.get(entry.id)
  const merged: TaskEntry = {
    ...(existing ?? {}),
    ...entry,
    outputExcerpt: entry.outputExcerpt ?? existing?.outputExcerpt ?? null,
    artifactPath: entry.artifactPath ?? existing?.artifactPath ?? null,
    error: entry.error ?? existing?.error ?? null,
    ctreeNodeId: entry.ctreeNodeId ?? existing?.ctreeNodeId ?? null,
    ctreeSnapshot: entry.ctreeSnapshot ?? existing?.ctreeSnapshot ?? null,
    updatedAt: entry.updatedAt || existing?.updatedAt || Date.now(),
  }
  this.taskMap.set(merged.id, merged)
  const taskEntries = Array.from(this.taskMap.values()) as TaskEntry[]
  this.tasks = taskEntries.sort((a, b) => b.updatedAt - a.updatedAt)
}

export function handleTaskEvent(
  this: any,
  payload: Record<string, unknown>,
  options?: {
    readonly eventType?: string
    readonly eventId?: string | null
    readonly seq?: number | null
    readonly timestamp?: number | null
  },
): void {
  const taskId =
    extractString(payload, ["task_id", "taskId", "id"]) ??
    extractString(payload, ["session_id", "sessionId"])
  if (!taskId) return
  const kind = extractString(payload, ["kind", "event", "type"])
  const status = normalizeTaskStatus(extractString(payload, ["status", "state"]), kind ?? undefined)
  const description = extractString(payload, ["description", "title", "prompt"])
  const subagentType = extractString(payload, ["subagent_type", "subagentType"])
  const outputExcerpt = extractString(payload, ["output_excerpt", "output", "result", "message"])
  const error = extractString(payload, ["error"])
  const ctreeNodeId = extractString(payload, ["ctree_node_id", "ctreeNodeId"])
  const ctreeSnapshot = isRecord(payload.ctree_snapshot) ? (payload.ctree_snapshot as CTreeSnapshot) : null
  const artifact = isRecord(payload.artifact) ? payload.artifact : null
  const artifactPath =
    extractString(payload, ["artifact_path", "artifactPath", "artifact"]) ??
    (artifact ? extractString(artifact, ["path", "file"]) : undefined)
  const updatedAt = numberOrUndefined(payload.timestamp) ?? Date.now()
  const sessionId = extractString(payload, ["session_id", "sessionId"])
  this.upsertTask({
    id: taskId,
    sessionId: sessionId ?? null,
    description: description ?? null,
    subagentType: subagentType ?? null,
    status: status ?? null,
    kind: kind ?? null,
    outputExcerpt: outputExcerpt ? outputExcerpt.slice(0, 400) : null,
    artifactPath: artifactPath ?? null,
    error: error ?? null,
    ctreeNodeId: ctreeNodeId ?? null,
    ctreeSnapshot,
    updatedAt,
  })
  this.enqueueWorkGraphEvent?.(payload, {
    eventType: options?.eventType ?? "task_event",
    eventId: options?.eventId ?? null,
    seq: options?.seq ?? this.currentEventSeq ?? null,
    timestamp: options?.timestamp ?? updatedAt,
  })
}

export function updateUsageFromPayload(this: any, payload: Record<string, unknown>): void {
  const usage = extractUsageMetrics(payload)
  if (!usage) return
  this.stats.usage = { ...(this.stats.usage ?? {}), ...usage }
}

export function trimToolHistory(this: any): void {
  if (this.toolEvents.length <= MAX_TOOL_HISTORY) return
  const excess = this.toolEvents.length - MAX_TOOL_HISTORY
  const removed = this.toolEvents.splice(0, excess)
  for (const entry of removed) {
    if (entry.kind === "call" && entry.callId) {
      const mapped = this.toolLogEntryByCallId.get(entry.callId)
      if (mapped === entry.id) {
        this.toolLogEntryByCallId.delete(entry.callId)
      }
    }
  }
}

export function addTool(this: any,
  kind: ToolLogKind,
  text: string,
  status?: LiveSlotStatus,
  options?: { callId?: string | null; insertAfterId?: string | null; display?: ToolDisplayPayload | null },
): ToolLogEntry {
  assertDurableTranscriptSafe(text, { surface: "tool", kind }, `tool:${kind}`)
  const insertAfterId = options?.insertAfterId ?? null
  if (!insertAfterId && this.toolEvents.length > 0) {
    const last = this.toolEvents[this.toolEvents.length - 1] as ToolLogEntry
    const mergeableKind = kind === "call" || kind === "result"
    const lastMergeable = last.kind === "call" || last.kind === "result"
    const sameText = last.text === text
    const nextHeader = text.split(/\r?\n/)[0]?.trim()
    const lastHeader = last.text.split(/\r?\n/)[0]?.trim()
    const sameHeader = Boolean(nextHeader && lastHeader && nextHeader === lastHeader)
    const callId = options?.callId ?? null
    const display = options?.display ?? null
    const sameCall = callId && last.callId ? callId === last.callId : true
    if (mergeableKind && lastMergeable && (sameText || sameHeader) && sameCall) {
      const nextKind = kind === "result" || last.kind === "result" ? "result" : last.kind
      const merged: ToolLogEntry = {
        ...last,
        kind: nextKind,
        text,
        status: status ?? last.status,
        callId: last.callId ?? callId,
        display: display ?? last.display ?? null,
      }
      this.toolEvents[this.toolEvents.length - 1] = merged
      return merged
    }
  }
  const entry: ToolLogEntry = {
    id: createSlotId(),
    kind,
    text,
    status,
    callId: options?.callId ?? null,
    display: options?.display ?? null,
    createdAt: resolveTranscriptTimestamp(this),
  }
  if (insertAfterId) {
    const index = this.toolEvents.findIndex((item: ToolLogEntry) => item.id === insertAfterId)
    if (index >= 0) {
      this.toolEvents.splice(index + 1, 0, entry)
    } else {
      this.toolEvents.push(entry)
    }
  } else {
    this.toolEvents.push(entry)
  }
  this.trimToolHistory()
  return entry
}

export function updateToolEntry(
  this: any,
  entryId: string,
  patch: Partial<Omit<ToolLogEntry, "id" | "createdAt">>,
): ToolLogEntry | null {
  const index = this.toolEvents.findIndex((item: ToolLogEntry) => item.id === entryId)
  if (index < 0) return null
  const current = this.toolEvents[index] as ToolLogEntry
  const next: ToolLogEntry = {
    ...current,
    ...patch,
    id: current.id,
    createdAt: current.createdAt,
  }
  assertDurableTranscriptSafe(next.text, { surface: "tool", kind: next.kind }, `tool:${next.kind}:update`)
  this.toolEvents[index] = next
  return next
}

export function formatToolSlot(this: any, payload: unknown): { text: string; color?: string; summary?: string } {
  const data = isRecord(payload) ? payload : {}
  const toolName = extractString(data, ["tool", "name", "command"]) ?? "Tool"
  const action = extractString(data, ["action", "method", "kind"]) ?? "running"
  const progress = extractProgress(data)
  const progressText = progress != null ? ` (${Math.round(progress)}%)` : ""
  return { text: `${toolName}: ${action}${progressText}`, color: BRAND_COLORS.duneOrange, summary: this.extractDiffSummary(payload) }
}

const normalizeDisplayLines = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((line) => (typeof line === "string" ? line.trimEnd() : ""))
      .filter((line) => line.trim().length > 0)
  }
  if (typeof value === "string") {
    return value
      .split(/\r?\n/)
      .map((line) => line.trimEnd())
      .filter((line) => line.trim().length > 0)
  }
  return []
}

const TOOL_PREVIEW_MAX_LINES = 24

const normalizeToolArgs = (
  payload: Record<string, unknown>,
): { name: string | null; args: Record<string, unknown> | null } => {
  const toolRecord = isRecord(payload.tool) ? payload.tool : isRecord(payload.tool_call) ? payload.tool_call : null
  const name =
    extractString((toolRecord ?? {}) as Record<string, unknown>, ["name", "tool", "command"]) ??
    extractString(payload, ["tool_name", "tool", "name", "command"]) ??
    null
  const args = isRecord(toolRecord?.args) ? toolRecord?.args : isRecord(payload.args) ? payload.args : null
  return { name, args }
}

const normalizePreviewLines = (value: string): string[] => {
  const normalized = value.replace(/\r\n?/g, "\n")
  const lines = normalized.split("\n")
  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop()
  }
  return lines
}

const truncatePreviewLines = (lines: string[], maxLines: number) => {
  if (lines.length <= maxLines) return { lines, hidden: 0 }
  return { lines: lines.slice(0, maxLines), hidden: lines.length - maxLines }
}

const extractEmbeddedToolExecOutput = (
  payload: Record<string, unknown>,
): { stdout: string; stderr: string } | null => {
  const candidates: unknown[] = [
    payload.result,
    payload.out,
    payload.output,
    isRecord(payload.message) ? payload.message.content : null,
  ]
  for (const candidate of candidates) {
    if (!isRecord(candidate)) continue
    const stdout = typeof candidate.stdout === "string" ? candidate.stdout : ""
    const stderr = typeof candidate.stderr === "string" ? candidate.stderr : ""
    const fallback =
      !stdout && !stderr && typeof candidate.__mvi_text_output === "string" ? candidate.__mvi_text_output : ""
    const resolvedStdout = stdout || (!stderr ? fallback : "")
    if (resolvedStdout || stderr) {
      return { stdout: resolvedStdout, stderr }
    }
  }
  return null
}

const normalizeShellDisplayTitle = (title: unknown): string | null => {
  if (typeof title !== "string") return null
  const trimmed = title.trim()
  if (!trimmed) return null
  const normalized = trimmed.toLowerCase()
  if (normalized === "run_shell" || normalized === "shell_command" || normalized === "bash" || normalized === "bash.run") {
    return "Tool"
  }
  return trimmed
}

const summarizeShellDisplayDetail = (detailLines: string[]): string | null => {
  if (detailLines.length === 0) return null
  const hasStdoutLabel = detailLines[0] === "stdout:"
  const stderrIndex = detailLines.indexOf("stderr:")
  if (hasStdoutLabel && stderrIndex >= 0) {
    const stdoutLines = detailLines.slice(1, stderrIndex).filter((line) => line.trim().length > 0)
    const stderrLines = detailLines.slice(stderrIndex + 1).filter((line) => line.trim().length > 0)
    const parts: string[] = []
    if (stdoutLines.length > 0) parts.push(`stdout ${stdoutLines.length} ${stdoutLines.length === 1 ? "line" : "lines"}`)
    if (stderrLines.length > 0) parts.push(`stderr ${stderrLines.length} ${stderrLines.length === 1 ? "line" : "lines"}`)
    return parts.join(" · ") || null
  }
  if (detailLines[0] === "stderr:") {
    const stderrLines = detailLines.slice(1).filter((line) => line.trim().length > 0)
    return stderrLines.length > 0 ? `stderr ${stderrLines.length} ${stderrLines.length === 1 ? "line" : "lines"}` : null
  }
  const stdoutLines = detailLines.filter((line) => line.trim().length > 0)
  return stdoutLines.length > 0 ? `stdout ${stdoutLines.length} ${stdoutLines.length === 1 ? "line" : "lines"}` : null
}

export function resolveToolDisplayPayload(this: any, payload: Record<string, unknown>): Record<string, unknown> | null {
  const base = isRecord(payload.display) ? { ...payload.display } : {}
  const { name, args } = normalizeToolArgs(payload)
  const normalizedName = (name ?? "").toLowerCase()
  const pathValue = typeof args?.path === "string" ? args.path.trim() : null
  const payloadArtifactRef =
    normalizeArtifactRef((payload as Record<string, unknown>).artifact_ref) ??
    normalizeArtifactRef((payload as Record<string, unknown>).result_artifact) ??
    normalizeArtifactRef(base.detail_artifact)
  if (payloadArtifactRef) {
    base.detail_artifact = payloadArtifactRef
  }

  const ensureTitle = (prefix: string) => {
    if (!base.title && pathValue) {
      base.title = `${prefix}(${pathValue})`
    }
  }

  const isWrite =
    normalizedName === "write_file" ||
    normalizedName === "write" ||
    normalizedName === "write-file"
  const isPatch =
    normalizedName === "apply_patch" ||
    normalizedName === "patch"

  if (isWrite) {
    ensureTitle("Write")
    const content = typeof args?.content === "string" ? args.content : null
    if (!base.detail && content) {
      const rawLines = normalizePreviewLines(content)
      if (shouldMaterializeToolArtifact(content, "tool_output")) {
        const artifactRef = materializeToolArtifactRef({
          kind: "tool_output",
          mime: "text/plain",
          content,
          previewMaxLines: TOOL_PREVIEW_MAX_LINES,
          note: "Inline output truncated to artifact reference.",
        })
        base.detail_artifact = artifactRef
        const previewLines = Array.isArray(artifactRef.preview?.lines) ? artifactRef.preview?.lines : []
        const omitted = Math.max(0, Number(artifactRef.preview?.omitted_lines ?? rawLines.length - previewLines.length))
        base.detail_truncated = {
          hidden: omitted,
          mode: "head_tail",
          tail: previewLines.length,
          hint: `see ${artifactRef.path}`,
        }
      } else {
        const { lines, hidden } = truncatePreviewLines(rawLines, TOOL_PREVIEW_MAX_LINES)
        base.detail = lines
        if (hidden > 0 && !isRecord(base.detail_truncated)) {
          base.detail_truncated = { hidden }
        }
      }
      if (!base.summary) {
        const hasArtifact = Boolean(base.detail_artifact)
        base.summary = `Wrote ${rawLines.length} ${rawLines.length === 1 ? "line" : "lines"}${hasArtifact ? " (artifact)" : ""}`
      }
    }
  }

  if (isPatch) {
    ensureTitle("Patch")
    const diff =
      typeof args?.diff === "string"
        ? args?.diff
        : typeof args?.patch === "string"
          ? args?.patch
          : typeof args?.unified_diff === "string"
            ? args?.unified_diff
            : typeof args?.unified === "string"
              ? args?.unified
              : null
    if (!Array.isArray(base.diff_blocks) || base.diff_blocks.length === 0) {
      if (diff) {
        const normalized = diff.replace(/(?:\r?\n)+$/, "")
        if (shouldMaterializeToolArtifact(normalized, "tool_diff")) {
          const artifactRef = materializeToolArtifactRef({
            kind: "tool_diff",
            mime: "text/x-diff",
            content: normalized,
            previewMaxLines: TOOL_PREVIEW_MAX_LINES,
            note: "Large unified diff exported to artifact.",
          })
          base.detail_artifact = artifactRef
          if (!base.summary) {
            const lineCount = normalizePreviewLines(normalized).length
            base.summary = `Patch ${lineCount} ${lineCount === 1 ? "line" : "lines"} (artifact)`
          }
        } else {
          base.diff_blocks = [
            {
              kind: "diff",
              filePath: pathValue,
              unified: normalized,
            },
          ]
        }
      }
    }
  }

  const isShellLike =
    normalizedName === "run_shell" ||
    normalizedName === "shell_command" ||
    normalizedName === "bash" ||
    normalizedName === "bash.run"

  if (isShellLike) {
    const normalizedShellTitle = normalizeShellDisplayTitle(base.title)
    if (normalizedShellTitle) {
      base.title = normalizedShellTitle
    }
    const existingDetailLines = normalizeDisplayLines(base.detail)
    if (existingDetailLines.length > 0 && !base.summary) {
      const derivedSummary = summarizeShellDisplayDetail(existingDetailLines)
      if (derivedSummary) {
        base.summary = derivedSummary
      }
    }
    const embeddedOutput = extractEmbeddedToolExecOutput(payload)
    const hasStructuredDetail =
      normalizeDisplayLines(base.detail).length > 0 ||
      (Array.isArray(base.diff_blocks) && base.diff_blocks.length > 0) ||
      Boolean(normalizeArtifactRef(base.detail_artifact))
    if (embeddedOutput && !hasStructuredDetail) {
      const formatted = this.formatToolExecOutput(embeddedOutput)
      const detailLines = buildToolExecDetailLines(embeddedOutput)
      const outputSummary = describeToolExecOutput(embeddedOutput)
      if (formatted && detailLines.length > 0) {
        base.title = typeof base.title === "string" && base.title.trim() ? base.title : "Tool"
        if (shouldMaterializeToolArtifact(formatted, "tool_output")) {
          const artifactRef = materializeToolArtifactRef({
            kind: "tool_output",
            mime: "text/plain",
            content: formatted,
            previewMaxLines: TOOL_PREVIEW_MAX_LINES,
            note: "Inline tool output truncated to artifact reference.",
          })
          base.detail_artifact = artifactRef
          const previewLines = Array.isArray(artifactRef.preview?.lines) ? artifactRef.preview?.lines : []
          const omitted = Math.max(0, Number(artifactRef.preview?.omitted_lines ?? detailLines.length - previewLines.length))
          base.detail_truncated = {
            hidden: omitted,
            mode: "head_tail",
            tail: previewLines.length,
            hint: `see ${artifactRef.path}`,
          }
        } else {
          const { lines, hidden } = truncatePreviewLines(detailLines, TOOL_PREVIEW_MAX_LINES)
          base.detail = lines
          if (hidden > 0) {
            base.detail_truncated = {
              hidden,
              ...(isRecord(base.detail_truncated) ? base.detail_truncated : {}),
            }
          }
        }
        if (!base.summary) {
          const hasArtifact = Boolean(base.detail_artifact)
          base.summary = `${outputSummary ?? `Output ${detailLines.length} ${detailLines.length === 1 ? "line" : "lines"}`}${hasArtifact ? " (artifact)" : ""}`
        }
      }
    }
  }

  // Preserve structured detail for transcript/detail inspection even when a
  // tool row also materializes an artifact reference. Inline renderers decide
  // whether to project detail or keep the shell compact.
  if (base.detail_artifact) {
    delete base.diff_blocks
  }

  return Object.keys(base).length > 0 ? base : null
}

export function formatToolDisplayText(this: any, payload: Record<string, unknown>): string {
  const icons = resolveIcons()
  const display = isRecord(payload.display) ? payload.display : null
  const title =
    extractString(display ?? {}, ["title"]) ??
    extractString(payload, ["tool_name", "tool", "name"]) ??
    "Tool"
  const debugRule = extractString(display ?? {}, ["debug_rule_id"])
  const titleWithDebug = debugRule ? `${title} ⟪${debugRule}⟫` : title
  const summaryLines = normalizeDisplayLines(display ? display.summary : undefined)
  const detailLines = normalizeDisplayLines(display ? display.detail : undefined)
  const lines: string[] = [titleWithDebug]
  const truncated = isRecord(display?.detail_truncated) ? display?.detail_truncated : null
  const hiddenCount = truncated && typeof truncated.hidden === "number" ? truncated.hidden : null
  const hint = truncated && typeof truncated.hint === "string" ? truncated.hint : null
  const mode = truncated && typeof truncated.mode === "string" ? truncated.mode : null
  const tailCount = truncated && typeof truncated.tail === "number" ? truncated.tail : null

  const contentLines =
    summaryLines.length > 0 || detailLines.length > 0 ? [...summaryLines, ...detailLines] : []
  if (detailLines.length > 0 && hiddenCount && hiddenCount > 0) {
    const summaryLine = `${icons.ellipsis} ${hiddenCount} lines hidden${hint ? ` — ${hint}` : ""}`
    if (mode === "head_tail" && tailCount && tailCount > 0 && tailCount < contentLines.length) {
      const head = contentLines.slice(0, contentLines.length - tailCount)
      const tail = contentLines.slice(-tailCount)
      contentLines.length = 0
      contentLines.push(...head, summaryLine, ...tail)
    } else {
      contentLines.push(summaryLine)
    }
  }
  if (contentLines.length > 0) {
    contentLines.forEach((line, index) => {
      const prefix = index === contentLines.length - 1 ? icons.treeBranch : icons.verticalLine
      lines.push(`${prefix} ${line}`)
    })
  }
  return lines.join("\n")
}

export function resolveToolCallId(this: any, payload: Record<string, unknown>): string | null {
  const direct =
    extractString(payload, ["tool_call_id", "toolCallId"]) ??
    extractString(payload, ["call_id", "callId"]) ??
    extractString(payload, ["id"])
  if (direct) return direct
  const nestedToolCall = isRecord(payload.tool_call) ? payload.tool_call : isRecord(payload.toolCall) ? payload.toolCall : null
  if (nestedToolCall) {
    return (
      extractString(nestedToolCall, ["tool_call_id", "toolCallId"]) ??
      extractString(nestedToolCall, ["call_id", "callId", "id"]) ??
      null
    )
  }
  return null
}

export function appendToolCallArgs(this: any, callId: string, delta: string): string {
  const existing = this.toolCallArgsById.get(callId) ?? ""
  const next = existing + delta
  this.toolCallArgsById.set(callId, next)
  return next
}

export function appendToolExecOutput(this: any, callId: string, stream: "stdout" | "stderr", chunk: string): void {
  if (!chunk) return
  const existing = this.toolExecOutputByCallId.get(callId) ?? { stdout: "", stderr: "" }
  const next = (existing[stream] + chunk).slice(-MAX_TOOL_EXEC_OUTPUT)
  existing[stream] = next
  this.toolExecOutputByCallId.set(callId, existing)
}

export function takeToolExecOutput(this: any, callId: string): { stdout: string; stderr: string } | null {
  const existing = this.toolExecOutputByCallId.get(callId)
  if (!existing) return null
  this.toolExecOutputByCallId.delete(callId)
  return existing
}

export function formatToolExecOutput(this: any, output: { stdout: string; stderr: string } | null): string | null {
  if (!output) return null
  const stdout = output.stdout?.trimEnd()
  const stderr = output.stderr?.trimEnd()
  if (!stdout && !stderr) return null
  if (stdout && stderr) {
    return `stdout:\n${stdout}\n\nstderr:\n${stderr}`
  }
  if (stdout) return stdout
  return stderr || null
}

const countOutputLines = (value: string | null | undefined): number => {
  if (!value) return 0
  const trimmed = value.trimEnd()
  if (!trimmed) return 0
  return trimmed.split(/\r?\n/).length
}

const describeToolExecOutput = (output: { stdout: string; stderr: string } | null): string | null => {
  if (!output) return null
  const stdout = output.stdout?.trimEnd() || ""
  const stderr = output.stderr?.trimEnd() || ""
  const stdoutLines = countOutputLines(stdout)
  const stderrLines = countOutputLines(stderr)
  if (stdoutLines === 0 && stderrLines === 0) return null
  const parts: string[] = []
  if (stdoutLines > 0) {
    parts.push(`stdout ${stdoutLines} ${stdoutLines === 1 ? "line" : "lines"}`)
  }
  if (stderrLines > 0) {
    parts.push(`stderr ${stderrLines} ${stderrLines === 1 ? "line" : "lines"}`)
  }
  return parts.join(" · ")
}

const buildToolExecDetailLines = (output: { stdout: string; stderr: string } | null): string[] => {
  if (!output) return []
  const stdout = output.stdout?.trimEnd() || ""
  const stderr = output.stderr?.trimEnd() || ""
  const stdoutLines = normalizePreviewLines(stdout)
  const stderrLines = normalizePreviewLines(stderr)
  if (stdoutLines.length === 0 && stderrLines.length === 0) return []
  if (stdoutLines.length > 0 && stderrLines.length === 0) return stdoutLines
  if (stdoutLines.length === 0 && stderrLines.length > 0) {
    return ["stderr:", ...stderrLines]
  }
  return ["stdout:", ...stdoutLines, "", "stderr:", ...stderrLines]
}

export function formatToolExecPreview(this: any, output: { stdout: string; stderr: string } | null): string | null {
  if (!output) return null
  const stderr = output.stderr?.trimEnd()
  const stdout = output.stdout?.trimEnd()
  const text = stderr || stdout
  if (!text) return null
  const lines = text.split(/\r?\n/)
  const tail = lines[lines.length - 1] ?? ""
  if (!tail.trim()) return null
  const clipped = tail.length > 80 ? `${tail.slice(-80)}` : tail
  return clipped
}

export function mergeToolExecOutputIntoDisplay(
  this: any,
  display: Record<string, unknown> | null,
  output: { stdout: string; stderr: string } | null,
): Record<string, unknown> | null {
  if (!output) return display
  const formatted = this.formatToolExecOutput(output)
  const detailLines = buildToolExecDetailLines(output)
  const outputSummary = describeToolExecOutput(output)
  if (!formatted || detailLines.length === 0) return display
  const base = display ? { ...display } : {}
  const hasStructuredDetail =
    normalizeDisplayLines(base.detail).length > 0 ||
    (Array.isArray(base.diff_blocks) && base.diff_blocks.length > 0) ||
    Boolean(normalizeArtifactRef(base.detail_artifact))
  if (hasStructuredDetail) return base

  const rawLines = detailLines
  if (rawLines.length === 0) return base
  if (shouldMaterializeToolArtifact(formatted, "tool_output")) {
    const artifactRef = materializeToolArtifactRef({
      kind: "tool_output",
      mime: "text/plain",
      content: formatted,
      previewMaxLines: TOOL_PREVIEW_MAX_LINES,
      note: "Inline tool output truncated to artifact reference.",
    })
    base.detail_artifact = artifactRef
    const previewLines = Array.isArray(artifactRef.preview?.lines) ? artifactRef.preview?.lines : []
    const omitted = Math.max(0, Number(artifactRef.preview?.omitted_lines ?? rawLines.length - previewLines.length))
    base.detail_truncated = {
      hidden: omitted,
      mode: "head_tail",
      tail: previewLines.length,
      hint: `see ${artifactRef.path}`,
    }
  } else {
    const { lines, hidden } = truncatePreviewLines(rawLines, TOOL_PREVIEW_MAX_LINES)
    base.detail = lines
    if (hidden > 0) {
      base.detail_truncated = {
        hidden,
        ...(isRecord(base.detail_truncated) ? base.detail_truncated : {}),
      }
    }
  }
  if (!base.summary) {
    const hasArtifact = Boolean(base.detail_artifact)
    base.summary = `${outputSummary ?? `Output ${rawLines.length} ${rawLines.length === 1 ? "line" : "lines"}`}${hasArtifact ? " (artifact)" : ""}`
  }
  return Object.keys(base).length > 0 ? base : null
}

export function extractDiffSummary(this: any, payload: unknown): string | undefined {
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

export function isToolResultError(this: any, payload: unknown): boolean {
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

export function pushHint(this: any, msg: string): void {
  if (isLifecycleNoiseHint(msg)) {
    return
  }
  const startupNoise =
    (typeof msg === "string" &&
      (msg === "Skills selection updated." || msg === "Skills catalog updated.")) ||
    false
  const startupIdle =
    !this.pendingResponse &&
    Array.isArray(this.conversation) &&
    this.conversation.length === 0 &&
    Array.isArray(this.toolEvents) &&
    this.toolEvents.length === 0 &&
    (this.stats?.lastTurn ?? null) == null
  if (startupNoise && startupIdle) {
    return
  }
  if (this.hints[this.hints.length - 1] === msg) {
    return
  }
  if (msg === "Log link available." && this.hints.includes(msg)) {
    return
  }
  this.hints.push(msg)
  if (this.hints.length > MAX_HINTS) this.hints.shift()
  this.emitChange()
}

export function pushRawEvent(this: any, event: SessionEvent): void {
  const snapshot = {
    type: event.type,
    seq: event.seq,
    id: event.id,
    payload: event.payload,
  }
  let raw = ""
  try {
    raw = JSON.stringify(snapshot)
  } catch {
    raw = String(event.type)
  }
  if (raw.length > MAX_RAW_EVENT_CHARS) {
    raw = `${raw.slice(0, MAX_RAW_EVENT_CHARS)}…`
  }
  const entry: ToolLogEntry = {
    id: `raw-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
    kind: "status",
    text: `[raw] ${raw}`,
    createdAt: Date.now(),
  }
  this.rawEvents.push(entry)
  if (this.rawEvents.length > MAX_RAW_EVENTS) {
    this.rawEvents.splice(0, this.rawEvents.length - MAX_RAW_EVENTS)
  }
}

export function emitChange(this: any): void {
  if (this.emitScheduled) return
  this.emitScheduled = true
  queueMicrotask(() => {
    this.emitScheduled = false
    this.emit("change", this.getState())
  })
}
