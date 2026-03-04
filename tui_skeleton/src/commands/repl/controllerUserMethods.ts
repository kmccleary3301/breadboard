import type { ReadSessionFileOptions } from "../../api/client.js"
import { ApiError } from "../../api/client.js"
import type { SessionFileContent, SessionFileInfo } from "../../api/types.js"
import { promises as fs } from "node:fs"
import path from "node:path"
import { DEFAULT_MODEL_ID } from "../../config/appConfig.js"
import { getModelCatalog } from "../../providers/modelCatalog.js"
import type {
  ModelMenuItem,
  SkillCatalog,
  SkillCatalogSources,
  SkillSelection,
} from "../../repl/types.js"
import type { ReplState } from "./controller.js"
import { DEBUG_WAIT, sleep } from "./controllerUtils.js"

const workspaceRootFor = (context: any): string => {
  const configured = context?.config?.workspace
  if (typeof configured === "string" && configured.trim().length > 0) {
    return path.resolve(configured)
  }
  return process.cwd()
}

const resolveWorkspacePath = (context: any, targetPath?: string): { root: string; resolved: string } => {
  const root = workspaceRootFor(context)
  const candidate = (targetPath ?? ".").trim()
  const relativeTarget = candidate === "" || candidate === "." ? "." : candidate.replace(/^\/+/, "")
  const resolved = path.resolve(root, relativeTarget)
  const rel = path.relative(root, resolved)
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new Error(`Path is outside workspace: ${targetPath ?? "."}`)
  }
  return { root, resolved }
}

const toSessionPath = (root: string, absolutePath: string): string => {
  const relative = path.relative(root, absolutePath)
  const normalized = relative.split(path.sep).join("/")
  return normalized.length > 0 ? normalized : "."
}

const localListFiles = async (context: any, targetPath?: string): Promise<SessionFileInfo[]> => {
  const { root, resolved } = resolveWorkspacePath(context, targetPath)
  const stats = await fs.stat(resolved)
  if (!stats.isDirectory()) {
    return [{
      path: toSessionPath(root, resolved),
      type: "file",
      size: stats.size,
      updated_at: stats.mtime.toISOString(),
    }]
  }
  const entries = await fs.readdir(resolved, { withFileTypes: true })
  const files: SessionFileInfo[] = []
  for (const entry of entries) {
    const full = path.join(resolved, entry.name)
    const entryStats = await fs.stat(full)
    const type: "file" | "directory" = entryStats.isDirectory() ? "directory" : "file"
    files.push({
      path: toSessionPath(root, full),
      type,
      size: type === "file" ? entryStats.size : undefined,
      updated_at: entryStats.mtime.toISOString(),
    })
  }
  files.sort((left, right) => {
    if (left.type !== right.type) return left.type === "directory" ? -1 : 1
    return left.path.localeCompare(right.path)
  })
  return files
}

const applySnippet = (
  content: string,
  options?: ReadSessionFileOptions,
): { content: string; truncated: boolean } => {
  if (options?.mode !== "snippet") {
    const maxBytes = typeof options?.maxBytes === "number" && options.maxBytes > 0 ? options.maxBytes : null
    if (!maxBytes || Buffer.byteLength(content, "utf8") <= maxBytes) {
      return { content, truncated: false }
    }
    const clipped = Buffer.from(content, "utf8").subarray(0, maxBytes).toString("utf8")
    return { content: clipped, truncated: true }
  }

  const normalized = content.replace(/\r\n/g, "\n")
  const lines = normalized.split("\n")
  const head = Math.max(1, options.headLines ?? 80)
  const tail = Math.max(1, options.tailLines ?? 80)
  if (lines.length <= head + tail + 1) {
    return { content: normalized, truncated: false }
  }
  const snippet = [...lines.slice(0, head), "", "...", "", ...lines.slice(-tail)].join("\n")
  return { content: snippet, truncated: true }
}

const localReadFile = async (
  context: any,
  targetPath: string,
  options?: ReadSessionFileOptions,
): Promise<SessionFileContent> => {
  const { root, resolved } = resolveWorkspacePath(context, targetPath)
  const stats = await fs.stat(resolved)
  if (!stats.isFile()) {
    throw new Error(`Path is not a file: ${targetPath}`)
  }
  const raw = await fs.readFile(resolved, "utf8")
  const snippet = applySnippet(raw, options)
  return {
    path: toSessionPath(root, resolved),
    content: snippet.content,
    truncated: snippet.truncated,
    total_bytes: stats.size,
  }
}

export async function dispatchSlashCommand(
  this: any,
  command: string,
  args: string[],
): Promise<void> {
  const handler = this.slashHandlers()[command]
  if (!handler) {
    this.pushHint(`Unknown command: /${command}`)
    return
  }
  await handler(args)
}

export async function listFiles(this: any, path?: string): Promise<SessionFileInfo[]> {
  if (!this.sessionId) {
    return await localListFiles(this, path)
  }
  try {
    return await this.api().listSessionFiles(this.sessionId, path)
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 409 || error.status === 404) {
        return await localListFiles(this, path)
      }
      throw new Error(`File listing failed (${error.status}).`)
    }
    return await localListFiles(this, path)
  }
}

export async function readFile(
  this: any,
  path: string,
  options?: ReadSessionFileOptions,
): Promise<SessionFileContent> {
  if (!path || !path.trim()) {
    throw new Error("File path is empty.")
  }
  if (!this.sessionId) {
    return await localReadFile(this, path, options)
  }
  try {
    return await this.api().readSessionFile(this.sessionId, path, options)
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.status === 409 || error.status === 404) {
        return await localReadFile(this, path, options)
      }
      throw new Error(`File read failed (${error.status}).`)
    }
    return await localReadFile(this, path, options)
  }
}

export async function openModelMenu(this: any): Promise<void> {
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

export async function openSkillsMenu(this: any): Promise<void> {
  if (this.skillsMenu.status !== "hidden") {
    this.pushHint("Skills picker already open. Use Esc to cancel or apply selection.")
    return
  }
  if (!this.sessionId) {
    this.pushHint("Session not ready yet.")
    return
  }
  this.status = "Loading skills…"
  this.skillsMenu = { status: "loading" }
  this.emitChange()
  try {
    const payload = await this.api().getSkillsCatalog(this.sessionId)
    const catalog = (payload?.catalog ?? {}) as SkillCatalog
    const selection = (payload?.selection ?? null) as SkillSelection | null
    const sources = (payload?.sources ?? null) as SkillCatalogSources | null
    this.skillsCatalog = catalog
    this.skillsSelection = selection
    this.skillsSources = sources
    this.skillsMenu = { status: "ready", catalog, selection, sources }
    this.status = "Skills ready"
  } catch (error) {
    if (this.skillsCatalog) {
      this.skillsMenu = {
        status: "ready",
        catalog: this.skillsCatalog,
        selection: this.skillsSelection,
        sources: this.skillsSources,
      }
      this.status = "Skills ready"
    } else {
      this.skillsMenu = { status: "error", message: `Failed to load skills: ${(error as Error).message}` }
      this.status = "Skills catalog unavailable"
    }
  }
  this.emitChange()
}

export async function openInspectMenu(this: any): Promise<void> {
  if (this.inspectMenu.status !== "hidden") {
    this.pushHint("Inspector already open. Use Esc to close or /inspect refresh.")
    return
  }
  if (!this.sessionId) {
    this.pushHint("Session not ready yet.")
    return
  }
  this.status = "Loading inspector…"
  this.inspectMenu = { status: "loading" }
  this.emitChange()
  try {
    const session = (await this.api().getSession(this.sessionId)) as unknown as Record<string, unknown>
    let skills: Record<string, unknown> | null = null
    try {
      skills = (await this.api().getSkillsCatalog(this.sessionId)) as unknown as Record<string, unknown>
    } catch {
      skills = null
    }
    try {
      const ctree = await this.api().getCtreeSnapshot(this.sessionId)
      this.ctreeSnapshot = (ctree ?? null) as unknown as any
    } catch {
      // ignore ctree refresh errors
    }
    const ctree = this.ctreeSnapshot ? (this.ctreeSnapshot as unknown as Record<string, unknown>) : null
    this.inspectMenu = { status: "ready", session, skills, ctree }
    this.status = "Inspector ready"
  } catch (error) {
    this.inspectMenu = { status: "error", message: (error as Error).message }
    this.status = "Inspector unavailable"
  }
  this.emitChange()
}

export async function refreshInspectMenu(this: any): Promise<void> {
  if (this.inspectMenu.status === "hidden") {
    await this.openInspectMenu()
    return
  }
  this.inspectMenu = { status: "loading" }
  this.emitChange()
  try {
    const session = (await this.api().getSession(this.sessionId)) as unknown as Record<string, unknown>
    let skills: Record<string, unknown> | null = null
    try {
      skills = (await this.api().getSkillsCatalog(this.sessionId)) as unknown as Record<string, unknown>
    } catch {
      skills = null
    }
    try {
      const ctree = await this.api().getCtreeSnapshot(this.sessionId)
      this.ctreeSnapshot = (ctree ?? null) as unknown as any
    } catch {
      // ignore ctree refresh errors
    }
    const ctree = this.ctreeSnapshot ? (this.ctreeSnapshot as unknown as Record<string, unknown>) : null
    this.inspectMenu = { status: "ready", session, skills, ctree }
    this.status = "Inspector refreshed"
  } catch (error) {
    this.inspectMenu = { status: "error", message: (error as Error).message }
    this.status = "Inspector unavailable"
  }
  this.emitChange()
}

export function closeInspectMenu(this: any): void {
  if (this.inspectMenu.status !== "hidden") {
    this.inspectMenu = { status: "hidden" }
    this.emitChange()
  }
}

export function closeSkillsMenu(this: any): void {
  if (this.skillsMenu.status !== "hidden") {
    this.skillsMenu = { status: "hidden" }
    this.emitChange()
  }
}

export async function applySkillsSelection(this: any, selection: SkillSelection): Promise<void> {
  const payload: Record<string, unknown> = {
    mode: selection.mode ?? "blocklist",
  }
  if (selection.allowlist && selection.allowlist.length > 0) {
    payload.allowlist = selection.allowlist
  }
  if (selection.blocklist && selection.blocklist.length > 0) {
    payload.blocklist = selection.blocklist
  }
  if (selection.profile) {
    payload.profile = selection.profile
  }
  const ok = await this.runSessionCommand("set_skills", payload, "Skills selection updated.")
  if (ok) {
    this.skillsSelection = selection
    if (this.skillsMenu.status === "ready") {
      this.skillsMenu = {
        status: "ready",
        catalog: this.skillsMenu.catalog,
        selection: selection,
        sources: this.skillsMenu.sources,
      }
    }
    this.status = "Skills updated"
    this.emitChange()
  }
}

export function closeModelMenu(this: any): void {
  if (this.modelMenu.status !== "hidden") {
    this.modelMenu = { status: "hidden" }
    this.emitChange()
  }
}

export async function selectModel(this: any, value: string): Promise<void> {
  this.closeModelMenu()
  await this.runSessionCommand("set_model", { model: value }, `Model switch requested (${value}).`)
  this.status = `Model request: ${value}`
  this.emitChange()
}

export async function runSessionCommand(
  this: any,
  command: string,
  payload?: Record<string, unknown>,
  successMessage?: string,
): Promise<boolean> {
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
    this.pushHint(successMessage ?? `Sent command \"${command}\".`)
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

export async function loadModelMenuItems(this: any): Promise<ModelMenuItem[]> {
  const catalog = await getModelCatalog({ configPath: this.config.configPath })
  const models = catalog.models
  const defaultModel = catalog.defaultModel ?? DEFAULT_MODEL_ID
  if (models.length === 0) {
    const fallbackIds = Array.from(
      new Set(
        [this.stats?.model, this.config?.model, defaultModel]
          .map((value) => (typeof value === "string" ? value.trim() : ""))
          .filter((value) => value.length > 0),
      ),
    )
    return fallbackIds.map<ModelMenuItem>((modelId) => {
      const providerRaw = modelId.includes("/") ? modelId.split("/", 1)[0] : "custom"
      const providerLabel = providerRaw
        .split(/[^a-z0-9]+/i)
        .filter((part) => part.length > 0)
        .map((part) => part[0].toUpperCase() + part.slice(1))
        .join(" ")
      return {
        label: `${providerLabel || "Custom"} · ${modelId}`,
        value: modelId,
        provider: providerRaw || "custom",
        detail: "local fallback",
        contextTokens: null,
        priceInPerM: null,
        priceOutPerM: null,
        isDefault: modelId === defaultModel,
        isCurrent: modelId === this.stats.model,
      }
    })
  }
  return models.map<ModelMenuItem>((model) => {
    const providerLabel = (() => {
      const raw = model.provider ?? "unknown"
      const lowered = raw.toLowerCase()
      if (lowered === "openrouter") return "OpenRouter"
      if (lowered === "openai") return "OpenAI"
      if (!raw.trim()) return "Unknown"
      return raw
        .split(/[^a-z0-9]+/i)
        .filter((part) => part.length > 0)
        .map((part) => part[0].toUpperCase() + part.slice(1))
        .join(" ")
    })()
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
      isDefault: model.id === defaultModel,
      isCurrent: model.id === this.stats.model,
    }
  })
}

export async function waitFor(
  this: any,
  predicate: (state: ReplState) => boolean,
  timeoutMs = 10_000,
): Promise<ReplState> {
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

export async function waitForCompletion(
  this: any,
  timeoutMs = 10_000,
): Promise<void> {
  const limit = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 10_000
  try {
    await this.waitFor((state: ReplState) => state.completionSeen, limit)
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
