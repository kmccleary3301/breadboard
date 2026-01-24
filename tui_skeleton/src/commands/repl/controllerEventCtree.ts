import { ApiError } from "../../api/client.js"
import type { CTreeTreeResponse, CTreeTreeSource, CTreeTreeStage } from "../../api/types.js"
import { formatErrorPayload } from "./controllerUtils.js"

export async function refreshCtreeTree(this: any, options?: {
  readonly stage?: CTreeTreeStage | string
  readonly includePreviews?: boolean
  readonly source?: CTreeTreeSource | string
  readonly silent?: boolean
}): Promise<void> {
  if (!this.sessionId) return
  const stage = (options?.stage ?? this.ctreeStage) as CTreeTreeStage | string
  const includePreviews = options?.includePreviews ?? this.ctreeIncludePreviews
  const source = (options?.source ?? this.ctreeSource) as CTreeTreeSource | string
  this.ctreeTreeRequested = true
  this.ctreeStage = stage
  this.ctreeIncludePreviews = includePreviews
  this.ctreeSource = source
  if (this.ctreeRefreshInFlight) {
    this.ctreeRefreshPending = true
    return
  }
  const showLoading = !options?.silent || !this.ctreeTree
  if (showLoading) {
    this.ctreeTreeStatus = "loading"
    this.ctreeTreeError = null
    this.emitChange()
  }
  this.ctreeRefreshInFlight = true
  try {
    const tree = await this.api().getCtreeTree(this.sessionId, {
      stage: stage || undefined,
      includePreviews,
      source: source || undefined,
    })
    this.ctreeTree = (tree ?? null) as CTreeTreeResponse | null
    this.ctreeTreeStatus = "idle"
    this.ctreeTreeError = null
    this.ctreeUpdatedAt = Date.now()
  } catch (error) {
    const message =
      error instanceof ApiError
        ? error.body
          ? formatErrorPayload(error.body)
          : error.message
        : (error as Error).message
    this.ctreeTreeStatus = "error"
    this.ctreeTreeError = message
    if (!options?.silent) {
      this.pushHint(`CTree refresh failed: ${message}`)
    }
  } finally {
    this.ctreeRefreshInFlight = false
    this.emitChange()
    if (this.ctreeRefreshPending) {
      this.ctreeRefreshPending = false
      this.scheduleCtreeRefresh()
    }
  }
}

export async function requestCtreeTree(this: any, force = false): Promise<void> {
  if (!this.sessionId) return
  this.ctreeTreeRequested = true
  if (this.ctreeTree && !force) return
  await this.refreshCtreeTree()
}

export async function setCtreeStage(this: any, stage: CTreeTreeStage | string): Promise<void> {
  const normalized = String(stage ?? "").trim().toUpperCase()
  if (!normalized) return
  await this.refreshCtreeTree({ stage: normalized })
}

export async function setCtreeSource(this: any, source: CTreeTreeSource | string): Promise<void> {
  const normalized = String(source ?? "").trim().toLowerCase()
  if (!normalized) return
  await this.refreshCtreeTree({ source: normalized })
}

export async function setCtreePreviews(this: any, includePreviews: boolean): Promise<void> {
  await this.refreshCtreeTree({ includePreviews })
}

export function scheduleCtreeRefresh(this: any): void {
  if (!this.ctreeTreeRequested) return
  if (this.ctreeRefreshInFlight) {
    this.ctreeRefreshPending = true
    return
  }
  if (this.ctreeRefreshTimer) {
    this.ctreeRefreshPending = true
    return
  }
  const lastUpdate = this.ctreeUpdatedAt ?? 0
  const elapsed = Date.now() - lastUpdate
  const delay = elapsed >= 450 ? 0 : 450 - elapsed
  this.ctreeRefreshTimer = setTimeout(() => {
    this.ctreeRefreshTimer = null
    void this.refreshCtreeTree({ silent: true })
  }, delay)
}
