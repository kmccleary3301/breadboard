import { createHash } from "node:crypto"
import type { Block, InlineNode } from "@stream-mdx/core/types"

const hashString = (value: string): string => createHash("sha256").update(value).digest("hex").slice(0, 12)

const listLinePattern = /^(\s*)([*+-]|\d+\.)\s+(.*)$/

const toListItemInline = (metaItems: unknown, index: number): InlineNode[] | undefined => {
  if (!Array.isArray(metaItems)) return undefined
  const item = metaItems[index]
  return Array.isArray(item) ? item as InlineNode[] : undefined
}

const splitListBlockIntoItems = (block: Block): Block[] => {
  const raw = block.payload?.raw ?? ""
  const lines = raw.split(/\r?\n/)
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  const items: Block[] = []
  let itemIndex = -1
  let current: string[] = []
  let currentInline: InlineNode[] | undefined

  const flush = () => {
    if (current.length === 0 || itemIndex < 0) return
    const itemRaw = current.join("\n")
    const followedByAnotherItem = itemIndex < Math.max(0, raw.split(/\r?\n/).filter((line) => listLinePattern.test(line)).length - 1)
    items.push({
      ...block,
      id: `${block.id}:item:${itemIndex}`,
      type: "list-item",
      // A streaming list block often remains unfinalized until the whole answer
      // completes. Earlier list items are stable enough to freeze once a later
      // item exists; the holdback policy keeps the hot tail live.
      isFinalized: block.isFinalized || followedByAnotherItem,
      payload: {
        ...block.payload,
        raw: itemRaw,
        inline: currentInline,
        meta: {
          ...meta,
          parentBlockId: block.id,
          itemIndex,
          sourceType: "list",
          promotionUnitHash: hashString(itemRaw),
        },
      },
    })
    current = []
    currentInline = undefined
  }

  for (const line of lines) {
    const match = listLinePattern.exec(line)
    if (match) {
      flush()
      itemIndex += 1
      current = [line]
      currentInline = toListItemInline(meta.items, itemIndex)
      continue
    }
    if (current.length > 0) {
      current.push(line)
    }
  }
  flush()

  return items.length > 0 ? items : [block]
}

export const expandMarkdownBlocksForPromotion = (blocks: ReadonlyArray<Block>): Block[] => {
  const expanded: Block[] = []
  for (const block of blocks) {
    if (block.type === "list") {
      expanded.push(...splitListBlockIntoItems(block))
      continue
    }
    expanded.push(block)
  }
  return expanded
}
