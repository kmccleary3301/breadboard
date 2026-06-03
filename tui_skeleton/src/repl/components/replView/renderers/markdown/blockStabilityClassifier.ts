import type { MarkdownBlockSnapshot } from "./streamMdxBlockStore.js"

export interface BlockStabilityPolicy {
  readonly holdbackRows: number
  readonly resizeQuietMs: number
  readonly now: number
  readonly lastResizeAt?: number | null
}

export interface BlockStabilityDecision {
  readonly blockId: string
  readonly eligible: boolean
  readonly reason: string
}

const conservativePromotableTypes = new Set(["paragraph", "heading", "blockquote", "list", "list-item"])

export const classifyStableBlocksForPromotion = (
  snapshots: ReadonlyArray<MarkdownBlockSnapshot>,
  policy: BlockStabilityPolicy,
): BlockStabilityDecision[] => {
  const resizeQuiet = policy.lastResizeAt == null || policy.now - policy.lastResizeAt >= policy.resizeQuietMs
  return snapshots.map((snapshot, index) => {
    if (!snapshot.isFinalized) return { blockId: snapshot.id, eligible: false, reason: "not-finalized" }
    if (!resizeQuiet) return { blockId: snapshot.id, eligible: false, reason: "resize-quiet-window" }
    if (index >= Math.max(0, snapshots.length - Math.max(1, policy.holdbackRows))) {
      return { blockId: snapshot.id, eligible: false, reason: "holdback-window" }
    }
    if (!conservativePromotableTypes.has(snapshot.type)) {
      return { blockId: snapshot.id, eligible: false, reason: `unsupported-type:${snapshot.type}` }
    }
    return { blockId: snapshot.id, eligible: true, reason: "finalized-stable-prefix" }
  })
}
