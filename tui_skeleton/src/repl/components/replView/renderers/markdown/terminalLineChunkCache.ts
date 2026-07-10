import { createHash } from "node:crypto"
import type { Block } from "@stream-mdx/core/types"
import { stripAnsiCodes } from "../../utils/ansi.js"
import { DEFAULT_DIFF_RENDER_STYLE } from "../diffStyles.js"
import { writeMarkdownMetricsDebugRecord } from "../../controller/qcDebugLog.js"
import { blocksToLines, type MarkdownRenderOptions } from "./streamMdxAdapter.js"

export interface RenderOptionsKey {
  readonly width: number | null
  readonly diffStyleHash: string
  readonly colorMode: "ansi"
  readonly asciiOnly: boolean
  readonly featureFlagsHash: string
}

export interface TerminalLineChunk {
  readonly chunkId: string
  readonly messageId: string | null
  readonly blockIds: readonly string[]
  readonly blockContentHash: string
  readonly renderOptionsKey: RenderOptionsKey
  readonly lines: readonly string[]
  readonly plainLines: readonly string[]
  readonly rowCount: number
  readonly createdAt: number
  readonly finalizedAt: number | null
  readonly source: "stream-mdx" | "fallback"
}

export interface TerminalLineChunkCacheStats {
  readonly hits: number
  readonly misses: number
  readonly entries: number
  readonly hitRate: number | null
}

const stableStringify = (value: unknown): string => {
  if (value === null || typeof value !== "object") return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`
  const entries = Object.entries(value as Record<string, unknown>)
    .filter(([, entryValue]) => entryValue !== undefined)
    .sort(([left], [right]) => left.localeCompare(right))
  return `{${entries.map(([key, entryValue]) => `${JSON.stringify(key)}:${stableStringify(entryValue)}`).join(",")}}`
}

const hashString = (value: string): string => createHash("sha256").update(value).digest("hex")

const renderRelevantPayload = (block: Block): Record<string, unknown> => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  const base: Record<string, unknown> = {
    raw: block.payload?.raw,
    inline: block.payload?.inline,
  }
  if (block.type === "heading") {
    base.level = meta.level ?? meta.depth
  } else if (block.type === "code") {
    base.lang = meta.lang ?? meta.language ?? meta.info
    base.diffBlocks = meta.diffBlocks
    base.codeLines = meta.codeLines
  } else if (block.type === "table") {
    base.header = meta.header
    base.rows = meta.rows
    base.align = meta.align
  } else if (block.type === "list") {
    base.items = meta.items
  } else if (block.type === "list-item") {
    base.itemIndex = meta.itemIndex
  }
  return base
}

export const hashMarkdownBlock = (block: Block): string =>
  hashString(stableStringify({
    id: block.id,
    type: block.type,
    payload: renderRelevantPayload(block),
  }))

export const hashMarkdownBlocks = (blocks: ReadonlyArray<Block>): string =>
  hashString(blocks.map((block) => hashMarkdownBlock(block)).join("\n"))

export const buildRenderOptionsKey = (options?: MarkdownRenderOptions): RenderOptionsKey => ({
  width: typeof options?.width === "number" && Number.isFinite(options.width) ? Math.floor(options.width) : null,
  diffStyleHash: hashString(stableStringify(options?.diffStyle ?? DEFAULT_DIFF_RENDER_STYLE)),
  colorMode: "ansi",
  asciiOnly: process.env.BREADBOARD_ASCII_ONLY === "1",
  featureFlagsHash: hashString(stableStringify({
    streamMdxLiveTokens: process.env.BREADBOARD_STREAM_MDX_LIVE_TOKENS === "1",
  })),
})

const buildCacheKey = (blocks: ReadonlyArray<Block>, options?: MarkdownRenderOptions): string =>
  hashString(stableStringify({
    blockIds: blocks.map((block) => block.id),
    blockHash: hashMarkdownBlocks(blocks),
    renderOptionsKey: buildRenderOptionsKey(options),
  }))

const chunkIdFor = (messageId: string | null, blocks: ReadonlyArray<Block>, options?: MarkdownRenderOptions): string => {
  const prefix = messageId?.trim() || "anonymous-message"
  const blockIds = blocks.map((block) => block.id).join("+") || "empty"
  const keyHash = buildCacheKey(blocks, options).slice(0, 16)
  return `${prefix}/chunk/${blockIds}/${keyHash}`
}

export class TerminalLineChunkCache {
  private readonly chunks = new Map<string, TerminalLineChunk>()
  private hits = 0
  private misses = 0

  getOrCreate(
    blocks: ReadonlyArray<Block>,
    options?: MarkdownRenderOptions,
    context?: { readonly messageId?: string | null; readonly now?: number },
  ): TerminalLineChunk {
    const cacheKey = buildCacheKey(blocks, options)
    const existing = this.chunks.get(cacheKey)
    if (existing) {
      this.hits += 1
      writeMarkdownMetricsDebugRecord({
        event: "markdown_render_cache",
        result: "hit",
        messageId: existing.messageId,
        chunkId: existing.chunkId,
        blockIds: existing.blockIds,
        rowCount: existing.rowCount,
        renderOptionsKey: existing.renderOptionsKey,
        stats: this.stats(),
      })
      return existing
    }
    this.misses += 1
    const lines = blocksToLines(blocks, options)
    const now = context?.now ?? Date.now()
    const blockContentHash = hashMarkdownBlocks(blocks)
    const chunk: TerminalLineChunk = {
      chunkId: chunkIdFor(context?.messageId ?? null, blocks, options),
      messageId: context?.messageId ?? null,
      blockIds: blocks.map((block) => block.id),
      blockContentHash,
      renderOptionsKey: buildRenderOptionsKey(options),
      lines,
      plainLines: lines.map(stripAnsiCodes),
      rowCount: lines.length,
      createdAt: now,
      finalizedAt: blocks.every((block) => block.isFinalized) ? now : null,
      source: "stream-mdx",
    }
    this.chunks.set(cacheKey, chunk)
    writeMarkdownMetricsDebugRecord({
      event: "markdown_render_cache",
      result: "miss",
      messageId: chunk.messageId,
      chunkId: chunk.chunkId,
      blockIds: chunk.blockIds,
      rowCount: chunk.rowCount,
      renderOptionsKey: chunk.renderOptionsKey,
      stats: this.stats(),
    })
    return chunk
  }

  clear(): void {
    this.chunks.clear()
    this.hits = 0
    this.misses = 0
  }

  stats(): TerminalLineChunkCacheStats {
    const total = this.hits + this.misses
    return {
      hits: this.hits,
      misses: this.misses,
      entries: this.chunks.size,
      hitRate: total > 0 ? this.hits / total : null,
    }
  }
}

export const cachedBlocksToLines = (
  cache: TerminalLineChunkCache,
  blocks: ReadonlyArray<Block>,
  options?: MarkdownRenderOptions,
  context?: { readonly messageId?: string | null; readonly now?: number },
): string[] => [...cache.getOrCreate(blocks, options, context).lines]
