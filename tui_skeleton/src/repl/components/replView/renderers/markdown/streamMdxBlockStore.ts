import type { Block } from "@stream-mdx/core/types"
import { hashMarkdownBlock } from "./terminalLineChunkCache.js"

export interface MarkdownBlockSnapshot {
  readonly id: string
  readonly type: string
  readonly contentHash: string
  readonly isFinalized: boolean
  readonly order: number
  readonly block: Block
}

export interface MarkdownBlockStoreUpdate {
  readonly messageId: string
  readonly blockCount: number
  readonly changedBlockIds: readonly string[]
  readonly finalizedBlockIds: readonly string[]
  readonly stableFinalizedPrefixIds: readonly string[]
  readonly mutationAfterFrozenIds: readonly string[]
}

export class StreamMdxBlockStore {
  private readonly byMessage = new Map<string, MarkdownBlockSnapshot[]>()
  private readonly frozenHashes = new Map<string, string>()

  update(messageId: string, blocks: ReadonlyArray<Block>): MarkdownBlockStoreUpdate {
    const previous = this.byMessage.get(messageId) ?? []
    const previousById = new Map(previous.map((snapshot) => [snapshot.id, snapshot]))
    const snapshots = blocks.map((block, order): MarkdownBlockSnapshot => ({
      id: block.id,
      type: block.type,
      contentHash: hashMarkdownBlock(block),
      isFinalized: block.isFinalized,
      order,
      block,
    }))
    const changedBlockIds = snapshots
      .filter((snapshot) => previousById.get(snapshot.id)?.contentHash !== snapshot.contentHash)
      .map((snapshot) => snapshot.id)
    const finalizedBlockIds = snapshots.filter((snapshot) => snapshot.isFinalized).map((snapshot) => snapshot.id)
    const stableFinalizedPrefixIds: string[] = []
    for (const snapshot of snapshots) {
      if (!snapshot.isFinalized) break
      stableFinalizedPrefixIds.push(snapshot.id)
    }
    const mutationAfterFrozenIds = snapshots
      .filter((snapshot) => {
        const key = `${messageId}:${snapshot.id}`
        const frozenHash = this.frozenHashes.get(key)
        return frozenHash != null && frozenHash !== snapshot.contentHash
      })
      .map((snapshot) => snapshot.id)
    this.byMessage.set(messageId, snapshots)
    return {
      messageId,
      blockCount: snapshots.length,
      changedBlockIds,
      finalizedBlockIds,
      stableFinalizedPrefixIds,
      mutationAfterFrozenIds,
    }
  }

  get(messageId: string): readonly MarkdownBlockSnapshot[] {
    return this.byMessage.get(messageId) ?? []
  }

  markFrozen(messageId: string, blockIds: ReadonlyArray<string>): void {
    const snapshots = this.byMessage.get(messageId) ?? []
    const byId = new Map(snapshots.map((snapshot) => [snapshot.id, snapshot]))
    for (const blockId of blockIds) {
      const snapshot = byId.get(blockId)
      if (snapshot) this.frozenHashes.set(`${messageId}:${blockId}`, snapshot.contentHash)
    }
  }
}
