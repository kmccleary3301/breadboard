export function slashHandlers(this: any): Record<string, SlashHandler> {
  return {
    quit: async () => {
      this.pushHint("Exiting session…")
      this.status = "Exiting…"
      await this.stop()
    },
    stop: async () => {
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
      this.pushHint("Cleared view (history preserved).")
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
      this.addConversation("user", payload.content)
      try {
        await this.dispatchSubmission(payload, "Retrying prior input…")
      } catch {
        // handled in dispatchSubmission
      }
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
        this.updateViewPrefs({ showReasoning: enabled }, `Reasoning stream ${enabled ? "enabled" : "disabled"}.`)
        return
      }
      this.pushHint("Usage: /view collapse <auto|all|none>, /view scroll <auto|compact>, /view markdown <on|off>, /view raw <on|off>, /view tools <rail|inline|both|off>, /view reasoning <on|off>.")
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
  const entry: ConversationEntry = {
    id: this.nextConversationId(),
    speaker,
    text,
    phase: "final",
    createdAt: Date.now(),
  }
  this.conversation.push(entry)
}

export function setStreamingConversation(this: any, speaker: ConversationEntry["speaker"], text: string): void {
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
    createdAt: Date.now(),
  }
  this.conversation.push(entry)
  this.streamingEntryId = entry.id
}

export function finalizeStreamingEntry(this: any): void {
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

export function nextConversationId(this: any): string {
  this.conversationSequence += 1
  return `conv-${this.conversationSequence}`
}

export function upsertTask(this: any, entry: TaskEntry): void {
  const existing = this.taskMap.get(entry.id)
  const merged: TaskEntry = {
    ...(existing ?? {}),
    ...entry,
    updatedAt: entry.updatedAt || existing?.updatedAt || Date.now(),
  }
  this.taskMap.set(merged.id, merged)
  this.tasks = Array.from(this.taskMap.values()).sort((a, b) => b.updatedAt - a.updatedAt)
}

export function handleTaskEvent(this: any, payload: Record<string, unknown>): void {
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
  options?: { callId?: string | null; insertAfterId?: string | null },
): ToolLogEntry {
  const entry: ToolLogEntry = {
    id: createSlotId(),
    kind,
    text,
    status,
    callId: options?.callId ?? null,
    createdAt: Date.now(),
  }
  const insertAfterId = options?.insertAfterId ?? null
  if (insertAfterId) {
    const index = this.toolEvents.findIndex((item) => item.id === insertAfterId)
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

export function formatToolSlot(this: any, payload: unknown): { text: string; color?: string; summary?: string } {
  const data = isRecord(payload) ? payload : {}
  const toolName = extractString(data, ["tool", "name", "command"]) ?? "Tool"
  const action = extractString(data, ["action", "method", "kind"]) ?? "running"
  const progress = extractProgress(data)
  const progressText = progress != null ? ` (${Math.round(progress)}%)` : ""
  return { text: `${toolName}: ${action}${progressText}`, color: BRAND_COLORS.duneOrange, summary: this.extractDiffSummary(payload) }
}

export function resolveToolCallId(this: any, payload: Record<string, unknown>): string | null {
  return (
    extractString(payload, ["tool_call_id", "toolCallId"]) ??
    extractString(payload, ["call_id", "callId"]) ??
    extractString(payload, ["id"]) ??
    null
  )
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
