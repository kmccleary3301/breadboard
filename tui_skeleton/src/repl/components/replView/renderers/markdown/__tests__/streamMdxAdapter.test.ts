import { describe, it, expect } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { blocksToLines, formatInlineNodes } from "../streamMdxAdapter.js"
import { stripAnsiCodes } from "../../../utils/ansi.js"

const paragraphBlock = (raw: string): Block => ({
  id: raw,
  type: "paragraph",
  isFinalized: true,
  payload: { raw, inline: [{ kind: "text", text: raw }] },
})

describe("streamMdxAdapter", () => {
  it("preserves paragraph spacing between block-level elements", () => {
    const blocks: Block[] = [paragraphBlock("First para"), paragraphBlock("Second para")]
    const lines = blocksToLines(blocks)
    expect(lines).toEqual(["First para", "", "Second para"])
  })

  it("renders inline code distinctly", () => {
    const inline = formatInlineNodes([
      { kind: "text", text: "Use" },
      { kind: "code", text: "list_dir" },
    ])
    expect(inline).toContain("list_dir")
  })

  it("renders diffBlocks with tokenized content", () => {
    const blocks: Block[] = [
      {
        id: "code-1",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```diff\n+foo\n```",
          meta: {
            diffBlocks: [
              {
                kind: "diff",
                lines: [
                  {
                    kind: "add",
                    raw: "+foo",
                    tokens: [{ content: "foo", color: "#ff0000" }],
                  },
                ],
              },
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toContain("+foo")
  })

  it("falls back to codeLines when diffBlocks missing", () => {
    const blocks: Block[] = [
      {
        id: "code-2",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```diff\n+bar\n```",
          meta: {
            codeLines: [
              {
                text: "+bar",
                diffKind: "add",
                tokens: {
                  spans: [
                    {
                      t: "+bar",
                      v: { dark: { fg: "#00ff00" } },
                    },
                  ],
                },
              },
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("+bar")
  })

  it("falls back to raw diff line when tokens missing", () => {
    const blocks: Block[] = [
      {
        id: "code-3",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```diff\n-baz\n```",
          meta: {
            diffBlocks: [
              {
                kind: "diff",
                lines: [
                  {
                    kind: "del",
                    raw: "-baz",
                    tokens: null,
                  },
                ],
              },
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("-baz")
  })

  it("falls back to renderCodeLines when diff metadata missing", () => {
    const blocks: Block[] = [
      {
        id: "code-4",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```diff\n+qux\n```",
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("+qux")
  })
})
