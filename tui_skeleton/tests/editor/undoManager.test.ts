import { describe, expect, it } from "vitest"
import { UndoManager } from "../../src/repl/editor/undoManager.js"
import type { InputBufferState } from "../../src/repl/editor/types.js"

const makeState = (text: string, cursor = text.length): InputBufferState => ({
  text,
  cursor,
  chips: [],
  attachments: [],
})

describe("UndoManager", () => {
  it("undos and redoes in order", () => {
    const manager = new UndoManager()
    const before = makeState("hello", 5)
    const after = makeState("hello world", 11)
    manager.push({ before, after })

    expect(manager.canUndo()).toBe(true)
    expect(manager.undo()).toEqual(before)
    expect(manager.canRedo()).toBe(true)
    expect(manager.redo()).toEqual(after)
  })

  it("drops redo stack when pushing new frame", () => {
    const manager = new UndoManager()
    const s1 = makeState("foo")
    const s2 = makeState("foobar")
    const s3 = makeState("foobar baz")

    manager.push({ before: s1, after: s2 })
    manager.push({ before: s2, after: s3 })

    expect(manager.undo()).toEqual(s2)
    expect(manager.undo()).toEqual(s1)

    manager.push({ before: s1, after: s3 })
    expect(manager.canRedo()).toBe(false)
    expect(manager.redo()).toBeNull()
  })

  it("resets cleanly", () => {
    const manager = new UndoManager()
    const before = makeState("foo")
    const after = makeState("bar")
    manager.push({ before, after })
    manager.reset()
    expect(manager.canUndo()).toBe(false)
    expect(manager.undo()).toBeNull()
  })
})
