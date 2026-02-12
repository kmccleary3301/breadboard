import type { Worker } from "node:worker_threads"
import type { Block, WorkerIn, WorkerOut, Patch } from "@stream-mdx/core/types"
import { applyPatchBatch, createInitialSnapshot, type DocumentSnapshot } from "@stream-mdx/core"
import { createMarkdownWorkerThread, type MarkdownThreadOptions } from "./workerThread.js"

type Listener = (blocks: ReadonlyArray<Block>, meta?: { finalized?: boolean; error?: string | null }) => void

interface MarkdownStreamerOptions {
  readonly workerOptions?: MarkdownThreadOptions
  readonly prewarmLangs?: ReadonlyArray<string>
  readonly docPlugins?: WorkerIn extends { docPlugins?: infer P } ? P : Record<string, unknown>
}

const DEFAULT_PREWARM = ["typescript", "tsx", "javascript", "bash", "shell", "python", "json", "markdown", "diff", "yaml"]

type SnapshotNode = {
  id: string
  type: string
  parentId: string | null
  children: string[]
  props: Record<string, unknown>
}

const attachCodeLineMeta = (snapshot: DocumentSnapshot, blocks: ReadonlyArray<Block>): ReadonlyArray<Block> => {
  if (!snapshot?.nodes || blocks.length === 0) return blocks
  let changed = false
  const nextBlocks = blocks.map((block) => {
    if (block.type !== "code") return block
    const node = snapshot.nodes.get(block.id) as SnapshotNode | undefined
    if (!node || !Array.isArray(node.children) || node.children.length === 0) return block
    const codeLines = node.children
      .map((childId) => snapshot.nodes.get(childId) as SnapshotNode | undefined)
      .filter((child): child is SnapshotNode => Boolean(child && child.type === "code-line"))
      .map((child) => ({
        text: typeof child.props.text === "string" ? (child.props.text as string) : "",
        tokens: child.props.tokens ?? null,
        diffKind: child.props.diffKind ?? null,
        oldNo: typeof child.props.oldNo === "number" ? (child.props.oldNo as number) : child.props.oldNo ?? null,
        newNo: typeof child.props.newNo === "number" ? (child.props.newNo as number) : child.props.newNo ?? null,
      }))
    if (codeLines.length === 0) return block
    const meta = { ...(block.payload.meta ?? {}), codeLines }
    changed = true
    return { ...block, payload: { ...block.payload, meta } }
  })
  return changed ? nextBlocks : blocks
}

// Inline fallback used if worker cannot start.
const buildInlineBlocks = (buffer: string, finalized: boolean): Block[] => {
  const blocks: Block[] = []
  const lines = buffer.replace(/\r\n?/g, "\n").split("\n")
  let paragraph: string[] = []
  let fenceLines: string[] = []
  let fenceLang: string | undefined
  let inFence = false
  const flushParagraph = () => {
    if (paragraph.length === 0) return
    const content = paragraph.join("\n").trimEnd()
    if (content.length === 0) {
      paragraph = []
      return
    }
    blocks.push({
      id: `md-p-${blocks.length}`,
      type: "paragraph",
      isFinalized: finalized,
      payload: { raw: content },
    })
    paragraph = []
  }
  const flushFence = (forceFinalize: boolean) => {
    const code = fenceLines.join("\n")
    const lang = fenceLang
    blocks.push({
      id: `md-c-${blocks.length}`,
      type: "code",
      isFinalized: forceFinalize || finalized,
      payload: { raw: lang ? `\`\`\`${lang}\n${code}\n\`\`\`` : code, meta: lang ? { lang } : undefined },
    })
    fenceLines = []
    fenceLang = undefined
    inFence = false
  }
  for (const line of lines) {
    if (inFence) {
      if (line.trim().startsWith("```")) {
        flushFence(true)
      } else {
        fenceLines.push(line)
      }
      continue
    }
    if (line.trim().startsWith("```")) {
      flushParagraph()
      inFence = true
      const info = line.trim().slice(3).trim()
      fenceLang = info.length > 0 ? info : undefined
      continue
    }
    if (line.startsWith("#")) {
      flushParagraph()
      const match = /^(#+)\s*(.*)$/.exec(line.trim())
      const level = match ? Math.min(6, match[1].length) : 1
      const text = match && match[2] ? match[2] : line.replace(/^#+\s*/, "")
      blocks.push({
        id: `md-h-${blocks.length}`,
        type: "heading",
        isFinalized: finalized,
        payload: { raw: text, meta: { level } },
      })
      continue
    }
    if (line.trim().startsWith(">")) {
      flushParagraph()
      blocks.push({
        id: `md-bq-${blocks.length}`,
        type: "blockquote",
        isFinalized: finalized,
        payload: { raw: line.replace(/^>\s?/, "") },
      })
      continue
    }
    if (line.trim().length === 0) {
      flushParagraph()
      continue
    }
    paragraph.push(line)
  }
  if (inFence) {
    flushFence(false)
  } else {
    flushParagraph()
  }
  if (blocks.length === 0 && buffer.length > 0) {
    blocks.push({
      id: "md-fallback",
      type: "paragraph",
      isFinalized: finalized,
      payload: { raw: buffer },
    })
  }
  if (!finalized && blocks.length > 0) {
    const last = blocks[blocks.length - 1]
    if (last.isFinalized) {
      blocks[blocks.length - 1] = { ...last, isFinalized: false }
    }
  }
  return blocks
}

export class MarkdownStreamer {
  private readonly listeners = new Set<Listener>()
  private readonly prewarm: ReadonlyArray<string>
  private readonly docPlugins?: MarkdownStreamerOptions["docPlugins"]
  private readonly workerOptions?: MarkdownThreadOptions
  private worker: Worker | null = null
  private snapshot: DocumentSnapshot = createInitialSnapshot()
  private pendingChunks: string[] = []
  private pendingFinalize = false
  private ready = false
  private disposed = false
  private errored: string | null = null
  private inlineBuffer = ""
  private inlineMode = false
  private finalized = false

  constructor(options: MarkdownStreamerOptions = {}) {
    this.prewarm = options.prewarmLangs ?? DEFAULT_PREWARM
    this.docPlugins = options.docPlugins
    const envBundle = process.env.BREADBOARD_MARKDOWN_WORKER_PATH
    if (envBundle && envBundle.trim().length > 0) {
      this.workerOptions = {
        ...(options.workerOptions ?? {}),
        workerBundle: envBundle,
      }
    } else {
      this.workerOptions = options.workerOptions
    }
  }

  initialize(initialContent = ""): void {
    if (this.disposed) return
    this.snapshot = createInitialSnapshot()
    this.ready = false
    this.pendingFinalize = false
    this.finalized = false
    this.inlineBuffer = initialContent
    this.spawnWorker(initialContent)
    if (this.inlineMode) {
      this.emit(false)
    }
  }

  append(chunk: string): void {
    if (this.disposed || this.errored || !chunk) return
    if (this.inlineMode || !this.worker) {
      this.inlineBuffer += chunk
      this.emit(false)
      return
    }
    if (!this.ready) {
      this.pendingChunks.push(chunk)
      return
    }
    this.worker.postMessage({ type: "APPEND", text: chunk } satisfies WorkerIn)
  }

  finalize(): void {
    if (this.disposed || this.errored) return
    this.finalized = true
    if (this.inlineMode || !this.worker) {
      this.emit(true)
      return
    }
    if (!this.ready) {
      this.pendingFinalize = true
      return
    }
    this.worker.postMessage({ type: "FINALIZE" } satisfies WorkerIn)
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    listener(this.currentBlocks(), { finalized: this.finalized && this.ready, error: this.errored })
    return () => this.listeners.delete(listener)
  }

  getBlocks(): ReadonlyArray<Block> {
    return this.currentBlocks()
  }

  getError(): string | null {
    return this.errored
  }

  dispose(forceTerminate = false): void {
    if (this.disposed) return
    this.disposed = true
    if (this.worker) {
      if (forceTerminate) {
        this.worker.terminate().catch(() => undefined)
      }
      this.worker.removeAllListeners?.()
      this.worker = null
    }
    this.listeners.clear()
  }

  private spawnWorker(initialContent: string): void {
    try {
      this.worker = createMarkdownWorkerThread(this.workerOptions)
    } catch (error) {
      this.errored = `Failed to start markdown worker: ${(error as Error).message}`
      this.inlineMode = true
      return
    }
    this.inlineMode = false
    this.worker.on("message", (message: WorkerOut) => this.handleWorkerMessage(message))
    this.worker.on("error", (error) => {
      this.errored = error.message || "Markdown worker error"
      this.inlineMode = true
      this.emit(true)
    })
    this.worker.on("exit", (code) => {
      if (!this.disposed && code !== 0) {
        this.errored = `Markdown worker exited with code ${code}`
        this.inlineMode = true
        this.emit(true)
      }
    })
    const initMessage: WorkerIn = {
      type: "INIT",
      initialContent,
      prewarmLangs: [...this.prewarm],
      docPlugins: this.docPlugins,
      mdx: { compileMode: "worker" },
    }
    this.worker.postMessage(initMessage)
  }

  private handleWorkerMessage(message: WorkerOut): void {
    if (this.disposed || this.inlineMode) return
    switch (message.type) {
      case "INITIALIZED": {
        this.ready = true
        this.snapshot = createInitialSnapshot(message.blocks)
        if (this.pendingChunks.length > 0) {
          for (const chunk of this.pendingChunks.splice(0)) {
            this.worker?.postMessage({ type: "APPEND", text: chunk } satisfies WorkerIn)
          }
        }
        if (this.pendingFinalize) {
          this.pendingFinalize = false
          this.worker?.postMessage({ type: "FINALIZE" } satisfies WorkerIn)
        }
        this.emit(false)
        break
      }
      case "PATCH": {
        applyPatchBatch(this.snapshot, message.patches as Patch[])
        this.emit(false)
        break
      }
      case "RESET": {
        this.snapshot = createInitialSnapshot()
        this.ready = false
        this.emit(false)
        break
      }
      case "ERROR": {
        this.errored = message.error?.message ?? "Markdown worker error"
        this.inlineMode = true
        this.emit(true)
        break
      }
      default:
        break
    }
  }

  private currentBlocks(): ReadonlyArray<Block> {
    if (this.inlineMode || !this.worker) {
      return buildInlineBlocks(this.inlineBuffer, this.finalized || this.errored != null)
    }
    return attachCodeLineMeta(this.snapshot, this.snapshot.blocks)
  }

  private emit(finalized: boolean): void {
    const blocks = this.currentBlocks()
    for (const listener of this.listeners) {
      listener(blocks, { finalized, error: this.errored })
    }
  }
}
