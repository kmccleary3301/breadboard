import type { TerminalLineChunk } from "./terminalLineChunkCache.js"

export type BlockPaintState = "hot" | "held-stable" | "promoting" | "frozen-static"

export interface FrozenChunkFragment {
  readonly kind: "frozen-chunk"
  readonly messageId: string
  readonly chunkId: string
  readonly blockIds: readonly string[]
  readonly committedWidth: number | null
  readonly staticFeedItemId: string
  readonly rowCount: number
}

export interface HotTailFragment {
  readonly kind: "hot-tail"
  readonly messageId: string
  readonly blockIds: readonly string[]
  readonly renderWidth: number | null
  readonly rowCount: number | null
}

export type AssistantMessageFragment = FrozenChunkFragment | HotTailFragment

export interface AssistantMessageFragmentState {
  readonly messageId: string
  readonly fragments: readonly AssistantMessageFragment[]
  readonly blockStates: ReadonlyMap<string, BlockPaintState>
}

export const buildInitialFragmentState = (messageId: string, blockIds: ReadonlyArray<string>): AssistantMessageFragmentState => ({
  messageId,
  fragments: [
    {
      kind: "hot-tail",
      messageId,
      blockIds: [...blockIds],
      renderWidth: null,
      rowCount: null,
    },
  ],
  blockStates: new Map(blockIds.map((blockId) => [blockId, "hot" as const])),
})

export const applyFrozenChunkToFragmentState = (
  state: AssistantMessageFragmentState,
  chunk: TerminalLineChunk,
  staticFeedItemId: string,
): AssistantMessageFragmentState => {
  const promoted = new Set(chunk.blockIds)
  const blockStates = new Map(state.blockStates)
  for (const blockId of promoted) blockStates.set(blockId, "frozen-static")

  const fragments: AssistantMessageFragment[] = []
  let inserted = false
  for (const fragment of state.fragments) {
    if (fragment.kind === "hot-tail") {
      const remaining = fragment.blockIds.filter((blockId) => !promoted.has(blockId))
      if (!inserted) {
        fragments.push({
          kind: "frozen-chunk",
          messageId: state.messageId,
          chunkId: chunk.chunkId,
          blockIds: [...chunk.blockIds],
          committedWidth: chunk.renderOptionsKey.width,
          staticFeedItemId,
          rowCount: chunk.rowCount,
        })
        inserted = true
      }
      if (remaining.length > 0) {
        fragments.push({ ...fragment, blockIds: remaining })
      }
      continue
    }
    fragments.push(fragment)
  }

  if (!inserted) {
    fragments.push({
      kind: "frozen-chunk",
      messageId: state.messageId,
      chunkId: chunk.chunkId,
      blockIds: [...chunk.blockIds],
      committedWidth: chunk.renderOptionsKey.width,
      staticFeedItemId,
      rowCount: chunk.rowCount,
    })
  }

  return { messageId: state.messageId, fragments, blockStates }
}

export const findActiveFrozenOverlap = (state: AssistantMessageFragmentState): string[] => {
  const frozen = new Set<string>()
  const active = new Set<string>()
  for (const fragment of state.fragments) {
    if (fragment.kind === "frozen-chunk") {
      fragment.blockIds.forEach((blockId) => frozen.add(blockId))
    } else {
      fragment.blockIds.forEach((blockId) => active.add(blockId))
    }
  }
  return [...frozen].filter((blockId) => active.has(blockId))
}

export const hotTailBlockIds = (state: AssistantMessageFragmentState): string[] =>
  state.fragments.flatMap((fragment) => fragment.kind === "hot-tail" ? [...fragment.blockIds] : [])
