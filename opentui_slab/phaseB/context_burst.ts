import type { NormalizedEvent } from "./event_adapter.ts"

export type ContextBurstEntry = {
  readonly kind: "tool.call" | "tool.result"
  readonly toolName: string
  readonly ok?: boolean
  readonly summary?: string
}

export type ContextBurstState = {
  readonly count: number
  readonly toolCounts: Readonly<Record<string, number>>
  readonly failedCount: number
  readonly entries: ReadonlyArray<ContextBurstEntry>
}

export const isContextCollectionEvent = (
  normalized: Pick<NormalizedEvent, "type" | "toolClass">,
): boolean =>
  (normalized.type === "tool.call" || normalized.type === "tool.result") &&
  normalized.toolClass === "context_collection"

export const appendContextBurstEvent = (
  state: ContextBurstState | null,
  normalized: Pick<NormalizedEvent, "type" | "toolName" | "ok" | "summary">,
): ContextBurstState => {
  const toolName = (normalized.toolName ?? "context_tool").trim() || "context_tool"
  const next: Record<string, number> = { ...(state?.toolCounts ?? {}) }
  next[toolName] = (next[toolName] ?? 0) + 1
  const entry: ContextBurstEntry = {
    kind: normalized.type === "tool.call" ? "tool.call" : "tool.result",
    toolName,
    ok: normalized.ok,
    summary: normalized.summary?.short,
  }
  const entries = [...(state?.entries ?? []), entry].slice(-8)
  return {
    count: (state?.count ?? 0) + 1,
    toolCounts: next,
    failedCount: (state?.failedCount ?? 0) + (normalized.ok === false ? 1 : 0),
    entries,
  }
}

export const formatContextBurstSummary = (state: ContextBurstState): string => {
  const toolBits = Object.entries(state.toolCounts)
    .sort((a, b) => {
      if (b[1] !== a[1]) return b[1] - a[1]
      return a[0].localeCompare(b[0])
    })
    .slice(0, 4)
    .map(([name, count]) => `${name}×${count}`)

  const suffix = state.failedCount > 0 ? ` · ${state.failedCount} failed` : ""
  return `\n[context] ${state.count} ops · ${toolBits.join(" · ")}${suffix}\n`
}

export const formatContextBurstDetail = (state: ContextBurstState): string => {
  const recent = state.entries
    .slice(-4)
    .map((entry) => {
      const status =
        entry.kind === "tool.call"
          ? "call"
          : entry.ok === true
            ? "ok"
            : entry.ok === false
              ? "failed"
              : "result"
      return `${entry.toolName} ${status}`
    })
    .join(" · ")
  return recent || "no context tools recorded"
}

export const formatContextBurstBlock = (state: ContextBurstState): string => {
  const summary = formatContextBurstSummary(state).trim()
  const lines = [summary]
  const entries = state.entries.slice(-6)
  for (const entry of entries) {
    const status =
      entry.kind === "tool.call"
        ? "call"
        : entry.ok === true
          ? "ok"
          : entry.ok === false
            ? "failed"
            : "result"
    lines.push(`  - ${entry.toolName} (${status})`)
  }
  return `\n${lines.join("\n")}\n`
}
