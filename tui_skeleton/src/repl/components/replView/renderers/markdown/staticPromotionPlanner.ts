import type { Block } from "@stream-mdx/core/types"
import type { MarkdownRenderOptions } from "./streamMdxAdapter.js"
import { classifyStableBlocksForPromotion, type BlockStabilityPolicy } from "./blockStabilityClassifier.js"
import type { MarkdownBlockSnapshot } from "./streamMdxBlockStore.js"
import { TerminalLineChunkCache, type TerminalLineChunk } from "./terminalLineChunkCache.js"

export interface StaticPromotionPlan {
  readonly messageId: string
  readonly chunks: readonly TerminalLineChunk[]
  readonly hotTailBlockIds: readonly string[]
  readonly heldBlockIds: readonly string[]
  readonly duplicateChunkIds: readonly string[]
}

export interface StaticPromotionPlannerOptions {
  readonly messageId: string
  readonly snapshots: ReadonlyArray<MarkdownBlockSnapshot>
  readonly cache: TerminalLineChunkCache
  readonly renderOptions?: MarkdownRenderOptions
  readonly stabilityPolicy: BlockStabilityPolicy
  readonly alreadyPromotedChunkIds?: ReadonlySet<string>
  readonly alreadyFrozenBlockIds?: ReadonlySet<string>
  readonly now?: number
}

const contiguousEligibleGroups = (
  snapshots: ReadonlyArray<MarkdownBlockSnapshot>,
  eligibleIds: ReadonlySet<string>,
  alreadyFrozenBlockIds: ReadonlySet<string>,
): Block[][] => {
  const groups: Block[][] = []
  let current: Block[] = []
  for (const snapshot of snapshots) {
    if (eligibleIds.has(snapshot.id) && !alreadyFrozenBlockIds.has(snapshot.id)) {
      current.push(snapshot.block)
      continue
    }
    if (current.length > 0) {
      groups.push(current)
      current = []
    }
  }
  if (current.length > 0) groups.push(current)
  return groups
}

export const planStaticPromotions = (options: StaticPromotionPlannerOptions): StaticPromotionPlan => {
  const decisions = classifyStableBlocksForPromotion(options.snapshots, options.stabilityPolicy)
  const eligibleIds = new Set(decisions.filter((decision) => decision.eligible).map((decision) => decision.blockId))
  const alreadyFrozenBlockIds = options.alreadyFrozenBlockIds ?? new Set<string>()
  const groups = contiguousEligibleGroups(options.snapshots, eligibleIds, alreadyFrozenBlockIds)
  const chunks: TerminalLineChunk[] = []
  const duplicateChunkIds: string[] = []
  const alreadyPromotedChunkIds = options.alreadyPromotedChunkIds ?? new Set<string>()

  for (const group of groups) {
    const chunk = options.cache.getOrCreate(group, options.renderOptions, { messageId: options.messageId, now: options.now })
    if (alreadyPromotedChunkIds.has(chunk.chunkId)) {
      duplicateChunkIds.push(chunk.chunkId)
      continue
    }
    chunks.push(chunk)
  }

  const promotedBlockIds = new Set(chunks.flatMap((chunk) => [...chunk.blockIds]))
  const hotTailBlockIds = options.snapshots
    .filter((snapshot) => !alreadyFrozenBlockIds.has(snapshot.id) && !promotedBlockIds.has(snapshot.id))
    .map((snapshot) => snapshot.id)
  const heldBlockIds = decisions
    .filter((decision) => !decision.eligible && !alreadyFrozenBlockIds.has(decision.blockId))
    .map((decision) => decision.blockId)

  return {
    messageId: options.messageId,
    chunks,
    hotTailBlockIds,
    heldBlockIds,
    duplicateChunkIds,
  }
}
