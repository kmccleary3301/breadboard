import type { InputBufferState } from "./types.js"

export interface UndoFrame {
  readonly before: InputBufferState
  readonly after: InputBufferState
  readonly label?: string
}

/**
 * Lightweight undo stack tuned for the line editor. Each edit operation should enqueue exactly one
 * frame so that Ctrl+Z / Ctrl+Shift+Z feel predictable (especially for bulk pastes).
 */
export class UndoManager {
  private stack: UndoFrame[] = []
  private index = -1

  /**
   * Push a new frame and discard everything ahead of the current index (standard undo behavior).
   */
  push(frame: UndoFrame): void {
    if (this.index < this.stack.length - 1) {
      this.stack = this.stack.slice(0, this.index + 1)
    }
    this.stack.push(frame)
    this.index = this.stack.length - 1
  }

  canUndo(): boolean {
    return this.index >= 0
  }

  canRedo(): boolean {
    return this.index < this.stack.length - 1
  }

  undo(): InputBufferState | null {
    if (!this.canUndo()) return null
    const frame = this.stack[this.index]
    this.index -= 1
    return frame.before
  }

  redo(): InputBufferState | null {
    if (!this.canRedo()) return null
    this.index += 1
    const frame = this.stack[this.index]
    return frame.after
  }

  reset(): void {
    this.stack = []
    this.index = -1
  }
}
