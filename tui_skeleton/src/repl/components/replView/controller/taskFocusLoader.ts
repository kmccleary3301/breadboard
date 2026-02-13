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

const normalizeTailLines = (value: number): number => Math.max(8, Math.min(400, Math.floor(value)))

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
      return {
        ok: true,
        value: {
          lines,
          path: content.path,
          notice: `Loaded ${content.path} (${modeLabel})${truncated}`,
        },
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
