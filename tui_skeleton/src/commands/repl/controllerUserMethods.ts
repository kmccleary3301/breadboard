import type { ReadSessionFileOptions } from "../../api/client.js"
import { ApiError } from "../../api/client.js"
import type { SessionFileContent, SessionFileInfo } from "../../api/types.js"
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

export async function readFile(
  this: any,
  path: string,
  options?: ReadSessionFileOptions,
): Promise<SessionFileContent> {
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
