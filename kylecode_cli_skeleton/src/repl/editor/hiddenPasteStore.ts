import { randomUUID } from "node:crypto"
import type { PasteToken } from "./types.js"

/**
 * Keeps large pasted payloads out of the rendered string. We only render a chip, but the raw text
 * remains available for serialization, undo, and submission.
 */
export class HiddenPasteStore {
  private readonly store = new Map<string, string>()

  put(content: string): PasteToken {
    const id = randomUUID()
    this.store.set(id, content)
    return { id, length: content.length }
  }

  restore(id: string, content: string): void {
    this.store.set(id, content)
  }

  get(tokenId: string): string | undefined {
    return this.store.get(tokenId)
  }

  delete(tokenId: string): boolean {
    return this.store.delete(tokenId)
  }

  clear(): void {
    this.store.clear()
  }
}
