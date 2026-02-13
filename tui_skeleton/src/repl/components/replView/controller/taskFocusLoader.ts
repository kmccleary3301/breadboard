export interface TaskFocusSelectedTask {
  readonly id: string
  readonly artifactPath?: string | null
}

export interface TaskFocusReadResult {
  readonly path: string
  readonly content: string
  readonly truncated?: boolean
}

export interface TaskFocusReadOptions {
  readonly mode: "cat" | "snippet"
  readonly headLines?: number
  readonly tailLines?: number
  readonly maxBytes?: number
}

export interface TaskFocusLoadOptions {
  readonly rawMode: boolean
  readonly tailLines: number
  readonly maxBytes: number
  readonly disableCache?: boolean
  readonly cacheTtlMs?: number
  readonly nowMs?: number
}

export interface TaskFocusLoadSuccess {
  readonly lines: string[]
  readonly path: string
  readonly notice: string
}

export interface TaskFocusLoadFailure {
  readonly error: string
}

export interface TaskFocusLoadResult {
  readonly ok: boolean
  readonly value?: TaskFocusLoadSuccess
  readonly failure?: TaskFocusLoadFailure
}

const DEFAULT_TASK_FOCUS_CACHE_TTL_MS = 1200
const DEFAULT_TASK_FOCUS_CACHE_MAX_ENTRIES = 128

type TaskFocusCacheEntry = {
  readonly value: TaskFocusLoadSuccess
  readonly cachedAtMs: number
}

type TaskFocusCacheStats = {
  hits: number
  misses: number
  evictions: number
}

const taskFocusTailCache = new Map<string, TaskFocusCacheEntry>()
const taskFocusTailCacheStats: TaskFocusCacheStats = {
  hits: 0,
  misses: 0,
  evictions: 0,
}

const normalizeTailLines = (value: number): number => Math.max(8, Math.min(400, Math.floor(value)))
const normalizeTtlMs = (value: number | undefined): number =>
  value == null || !Number.isFinite(value)
    ? DEFAULT_TASK_FOCUS_CACHE_TTL_MS
    : Math.max(0, Math.floor(value))

const buildTaskFocusCacheKey = (task: TaskFocusSelectedTask, options: TaskFocusLoadOptions, tailLines: number): string => {
  const taskId = task.id.trim()
  const artifactPath = task.artifactPath?.trim() || ""
  const mode = options.rawMode ? "raw" : "snippet"
  return `${taskId}|${artifactPath}|${mode}|${tailLines}|${Math.max(1, Math.floor(options.maxBytes))}`
}

const getCacheNowMs = (options: TaskFocusLoadOptions): number =>
  typeof options.nowMs === "number" && Number.isFinite(options.nowMs) ? options.nowMs : Date.now()

const cacheGet = (key: string, nowMs: number, ttlMs: number): TaskFocusLoadSuccess | null => {
  const cached = taskFocusTailCache.get(key)
  if (!cached) {
    taskFocusTailCacheStats.misses += 1
    return null
  }
  if (ttlMs <= 0 || nowMs - cached.cachedAtMs > ttlMs) {
    taskFocusTailCache.delete(key)
    taskFocusTailCacheStats.misses += 1
    return null
  }
  taskFocusTailCache.delete(key)
  taskFocusTailCache.set(key, cached)
  taskFocusTailCacheStats.hits += 1
  return cached.value
}

const cacheSet = (key: string, value: TaskFocusLoadSuccess, nowMs: number): void => {
  if (taskFocusTailCache.has(key)) taskFocusTailCache.delete(key)
  taskFocusTailCache.set(key, { value, cachedAtMs: nowMs })
  if (taskFocusTailCache.size <= DEFAULT_TASK_FOCUS_CACHE_MAX_ENTRIES) return
  const first = taskFocusTailCache.keys().next().value
  if (typeof first === "string") {
    taskFocusTailCache.delete(first)
    taskFocusTailCacheStats.evictions += 1
  }
}

export const __resetTaskFocusTailCacheForTests = (): void => {
  taskFocusTailCache.clear()
  taskFocusTailCacheStats.hits = 0
  taskFocusTailCacheStats.misses = 0
  taskFocusTailCacheStats.evictions = 0
}

export const getTaskFocusTailCacheStats = (): Readonly<TaskFocusCacheStats & { size: number }> => ({
  ...taskFocusTailCacheStats,
  size: taskFocusTailCache.size,
})

export const buildTaskFocusArtifactCandidates = (task: TaskFocusSelectedTask): string[] => {
  const candidates: string[] = []
  if (task.artifactPath) candidates.push(task.artifactPath)
  const taskId = task.id
  candidates.push(`.breadboard/subagents/agent-${taskId}.jsonl`)
  candidates.push(`.breadboard/subagents/${taskId}.jsonl`)
  candidates.push(`.breadboard/subagents/${taskId}.json`)
  return candidates
}

export const loadTaskFocusTail = async (
  task: TaskFocusSelectedTask,
  options: TaskFocusLoadOptions,
  onReadFile: (path: string, options: TaskFocusReadOptions) => Promise<TaskFocusReadResult>,
): Promise<TaskFocusLoadResult> => {
  const candidates = buildTaskFocusArtifactCandidates(task)
  let lastError: string | null = null
  const tailLines = normalizeTailLines(options.tailLines)
  const ttlMs = normalizeTtlMs(options.cacheTtlMs)
  const cacheEnabled = options.disableCache !== true
  const nowMs = getCacheNowMs(options)
  const cacheKey = buildTaskFocusCacheKey(task, options, tailLines)
  if (cacheEnabled) {
    const cached = cacheGet(cacheKey, nowMs, ttlMs)
    if (cached) {
      return {
        ok: true,
        value: {
          ...cached,
          notice: `${cached.notice} [cache-hit]`,
        },
      }
    }
  }
  for (const pathCandidate of candidates) {
    try {
      const readMode: "cat" | "snippet" = options.rawMode ? "cat" : "snippet"
      const content = await onReadFile(pathCandidate, {
        mode: readMode,
        headLines: options.rawMode ? undefined : 0,
        tailLines: options.rawMode ? undefined : tailLines,
        maxBytes: options.maxBytes,
      })
      const lines = content.content.replace(/\r\n?/g, "\n").split("\n")
      const truncated = content.truncated ? " (truncated)" : ""
      const modeLabel = options.rawMode ? "raw" : `tail ${tailLines}`
      const value: TaskFocusLoadSuccess = {
        lines,
        path: content.path,
        notice: `Loaded ${content.path} (${modeLabel})${truncated}`,
      }
      if (cacheEnabled) cacheSet(cacheKey, value, nowMs)
      return {
        ok: true,
        value,
      }
    } catch (error) {
      lastError = (error as Error).message
    }
  }
  return {
    ok: false,
    failure: {
      error: lastError ?? "Unable to load task output.",
    },
  }
}
