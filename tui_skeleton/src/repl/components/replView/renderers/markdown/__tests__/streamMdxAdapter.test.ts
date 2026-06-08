import { describe, it, expect } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import { blocksToLines, blocksToLinesWithRawFallback, formatInlineNodes, renderMarkdownFallbackLines } from "../streamMdxAdapter.js"
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

  it("shows a language cue when stream-mdx supplies structured codeLines", () => {
    const blocks: Block[] = [
      {
        id: "code-lines-ts",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```ts\nconst answer = 42\n```",
          meta: {
            lang: "ts",
            codeLines: [
              {
                text: "const answer = 42",
                tokens: null,
              },
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("code · ts")
    expect(stripAnsiCodes(lines[1] ?? "")).toBe("const answer = 42")
  })

  it("renders common markdown structures without leaking raw markdown delimiters", () => {
    const blocks: Block[] = [
      {
        id: "heading-1",
        type: "heading",
        isFinalized: true,
        payload: {
          raw: "Sample Markdown",
          meta: { headingLevel: 1, headingText: "Sample Markdown" },
          inline: [{ kind: "text", text: "Sample Markdown" }],
        },
      },
      {
        id: "paragraph-inline",
        type: "paragraph",
        isFinalized: true,
        payload: {
          raw: "**Bold text**, *italic text*, and `inline code`.",
          inline: [
            { kind: "strong", children: [{ kind: "text", text: "Bold text" }] },
            { kind: "text", text: ", " },
            { kind: "em", children: [{ kind: "text", text: "italic text" }] },
            { kind: "text", text: ", and " },
            { kind: "code", text: "inline code" },
            { kind: "text", text: "." },
          ],
        },
      },
      {
        id: "quote-1",
        type: "blockquote",
        isFinalized: true,
        payload: {
          raw: "A blockquote for emphasis.",
          inline: [{ kind: "text", text: "A blockquote for emphasis." }],
        },
      },
      {
        id: "code-structured",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```python\ndef greet(name):\n    return f\"Hello, {name}!\"\n```",
          meta: { lang: "python" },
        },
      },
    ]
    const lines = blocksToLines(blocks).map(stripAnsiCodes)
    expect(lines).toContain("Sample Markdown")
    expect(lines).toContain("Bold text, italic text, and inline code.")
    expect(lines).toContain("A blockquote for emphasis.")
    expect(lines).toContain("code · python")
    expect(lines).toContain("def greet(name):")
    expect(lines.join("\n")).not.toMatch(/(^|\n)#{1,6}\s/)
    expect(lines.join("\n")).not.toContain("**Bold")
    expect(lines.join("\n")).not.toContain("*italic")
    expect(lines.join("\n")).not.toContain("`inline code`")
    expect(lines.join("\n")).not.toContain("> A blockquote")
    expect(lines.join("\n")).not.toContain("```")
  })

  it("renders fallback inline markdown without delimiter or padding artifacts", () => {
    const lines = renderMarkdownFallbackLines("**Bold text**, *italic text*, and `inline code`.", { width: 80 }).map(stripAnsiCodes)
    expect(lines).toEqual(["Bold text, italic text, and inline code."])
  })

  it("unwraps markdown-language fences into rendered terminal markdown", () => {
    const lines = renderMarkdownFallbackLines([
      "```markdown",
      "# Sample Markdown",
      "",
      "## Section Heading",
      "- **Bullet** item",
      "> A blockquote for emphasis.",
      "",
      "```python",
      "def greet(name):",
      "    return f\"Hello, {name}!\"",
      "```",
      "```",
    ].join("\n"), { width: 80 }).map(stripAnsiCodes)

    expect(lines).toContain("Sample Markdown")
    expect(lines).toContain("Section Heading")
    expect(lines).toContain("- Bullet item")
    expect(lines).toContain("A blockquote for emphasis.")
    expect(lines).toContain("code · python")
    expect(lines).toContain("def greet(name):")
    expect(lines.join("\n")).not.toContain("```markdown")
    expect(lines.join("\n")).not.toContain("# Sample Markdown")
    expect(lines.join("\n")).not.toContain("**Bullet**")
    expect(lines.join("\n")).not.toContain("> A blockquote")
  })

  it("unwraps markdown code blocks before honoring stream-mdx codeLines", () => {
    const blocks: Block[] = [
      {
        id: "markdown-code-with-token-lines",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```markdown\n# Sample Markdown\n- **Bullet** item\n```",
          meta: {
            lang: "markdown",
            code: "# Sample Markdown\n- **Bullet** item",
            codeLines: [
              { text: "# Sample Markdown", tokens: null },
              { text: "- **Bullet** item", tokens: null },
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks, { width: 80 }).map(stripAnsiCodes)
    expect(lines).toContain("Sample Markdown")
    expect(lines).toContain("- Bullet item")
    expect(lines.join("\n")).not.toContain("code · markdown")
    expect(lines.join("\n")).not.toContain("# Sample Markdown")
    expect(lines.join("\n")).not.toContain("**Bullet**")
  })

  it("renders long wrapper-fenced markdown without assistant-visible delimiter artifacts", () => {
    const lines = renderMarkdownFallbackLines([
      "```markdown",
      "# Long Markdown Response",
      "",
      "Paragraph with **Deep Bold** and *Deep Italic* plus `deep_inline_code`.",
      "",
      "> Long quote block that should not retain a raw greater-than marker.",
      "",
      "```python",
      "def long_markdown_probe():",
      "    return \"LONG-MARKDOWN-CODE-SENTINEL\"",
      "```",
      "",
      "END-LONG-MARKDOWN-SENTINEL",
      "```",
    ].join("\n"), { width: 84 }).map(stripAnsiCodes)

    const joined = lines.join("\n")
    expect(lines).toContain("Long Markdown Response")
    expect(joined).toContain("Paragraph with Deep Bold and Deep Italic plus deep_inline_code.")
    expect(joined).toContain("Long quote block that should not retain a raw greater-than marker.")
    expect(joined).toContain("LONG-MARKDOWN-CODE-SENTINEL")
    expect(joined).toContain("END-LONG-MARKDOWN-SENTINEL")
    expect(joined).not.toContain("code · markdown")
    expect(joined).not.toContain("```markdown")
    expect(joined).not.toContain("# Long Markdown Response")
    expect(joined).not.toContain("**Deep Bold**")
    expect(joined).not.toContain("*Deep Italic*")
    expect(joined).not.toContain("> Long quote")
  })

  it("keeps stream/final equivalence corpus semantically rendered", () => {
    const source = [
      "```markdown",
      "# Equivalence Heading",
      "",
      "Paragraph with **Equivalence Bold** and *Equivalence Italic* plus `equiv_inline`.",
      "",
      "> Equivalence quote should render without a raw marker.",
      "",
      "- **Bold equivalence item**",
      "- *Italic equivalence item*",
      "",
      "```ts",
      "export const equivalence = 'STREAM-FINAL-CODE-SENTINEL'",
      "```",
      "",
      "```diff",
      "-old",
      "+new",
      "```",
      "",
      "END-STREAM-FINAL-EQUIVALENCE",
      "```",
    ].join("\n")
    const lines = renderMarkdownFallbackLines(source, { width: 96 }).map(stripAnsiCodes)
    const joined = lines.join("\n")
    expect(joined).toContain("Equivalence Heading")
    expect(joined).toContain("Paragraph with Equivalence Bold and Equivalence Italic plus equiv_inline.")
    expect(joined).toContain("Equivalence quote should render without a raw marker.")
    expect(joined).toContain("- Bold equivalence item")
    expect(joined).toContain("- Italic equivalence item")
    expect(joined).toContain("STREAM-FINAL-CODE-SENTINEL")
    expect(joined).toContain("+new")
    expect(joined).toContain("END-STREAM-FINAL-EQUIVALENCE")
    expect(joined).not.toContain("code · markdown")
    expect(joined).not.toContain("```markdown")
    expect(joined).not.toContain("# Equivalence Heading")
    expect(joined).not.toContain("**Equivalence Bold**")
    expect(joined).not.toContain("*Equivalence Italic*")
    expect(joined).not.toContain("> Equivalence quote")
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

  it("falls back safely for unsupported fenced languages like mermaid", () => {
    const blocks: Block[] = [
      {
        id: "code-5",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```mermaid\ngraph TD\nA-->B\n```",
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("code · mermaid")
    expect(stripAnsiCodes(lines[1] ?? "")).toBe("graph TD")
    expect(stripAnsiCodes(lines[2] ?? "")).toBe("A-->B")
  })

  it("shows a stacked table cue when narrow layouts collapse columns", () => {
    const blocks: Block[] = [
      {
        id: "table-1",
        type: "table",
        isFinalized: true,
        payload: {
          raw: "| Column | Value |\n| --- | --- |\n| Alpha | 1 |",
          meta: {
            header: [
              [{ kind: "text", text: "Column" }],
              [{ kind: "text", text: "Value" }],
            ],
            rows: [
              [
                [{ kind: "text", text: "Alpha" }],
                [{ kind: "text", text: "1" }],
              ],
            ],
          },
        },
      },
    ]
    const lines = blocksToLines(blocks, { width: 24 })
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("table · stacked 2 cols")
    expect(stripAnsiCodes(lines[1] ?? "")).toContain("Column: Alpha")
    expect(stripAnsiCodes(lines[2] ?? "")).toContain("Value: 1")
  })

  it("shows a language cue before fenced code blocks", () => {
    const blocks: Block[] = [
      {
        id: "code-6",
        type: "code",
        isFinalized: true,
        payload: {
          raw: "```ts\nconst answer = 42\n```",
        },
      },
    ]
    const lines = blocksToLines(blocks)
    expect(stripAnsiCodes(lines[0] ?? "")).toBe("code · ts")
    expect(stripAnsiCodes(lines[1] ?? "")).toBe("const answer = 42")
  })

  it("renders transcript text that stream-mdx did not represent as rich blocks", () => {
    const blocks: Block[] = [
      {
        id: "table-tail",
        type: "table",
        isFinalized: true,
        payload: {
          raw: "| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |",
          meta: {
            header: [
              [{ kind: "text", text: "name" }],
              [{ kind: "text", text: "value" }],
            ],
            rows: [
              [
                [{ kind: "text", text: "alpha" }],
                [{ kind: "text", text: "1" }],
              ],
              [
                [{ kind: "text", text: "beta" }],
                [{ kind: "text", text: "2" }],
              ],
            ],
          },
        },
      },
    ]
    const rawText = "| name | value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |\nV6 markdown response 6 completed."
    const lines = blocksToLinesWithRawFallback(blocks, rawText, { width: 80 }).map(stripAnsiCodes)
    expect(lines).toContain("V6 markdown response 6 completed.")
  })

  it("does not append raw heading lines already represented by stream-mdx heading blocks", () => {
    const blocks: Block[] = [
      {
        id: "heading-projection",
        type: "heading",
        isFinalized: true,
        payload: {
          raw: "Projection",
          meta: { level: 3 },
          inline: [{ kind: "text", text: "Projection" }],
        },
      },
    ]
    const lines = blocksToLinesWithRawFallback(blocks, "### Projection\nTail", { width: 80 }).map(stripAnsiCodes)
    expect(lines.filter((line) => /Projection/.test(line))).toEqual(["Projection"])
    expect(lines).toContain("Tail")
  })

  it("does not duplicate post-list tail text when stream-mdx folds it into the final list item", () => {
    const blocks: Block[] = [
      {
        id: "list-tail",
        type: "list",
        isFinalized: false,
        payload: {
          raw: "- item 1\n- item 2\nV6 fuzz seed 31 profile mixed complete.",
          meta: {
            ordered: false,
            items: [
              [{ kind: "text", text: "item 1" }],
              [{ kind: "text", text: "item 2\nV6 fuzz seed 31 profile mixed complete." }],
            ],
          },
        },
      },
    ]
    const rawText = "- item 1\n- item 2\nV6 fuzz seed 31 profile mixed complete."
    const lines = blocksToLinesWithRawFallback(blocks, rawText, { width: 80 }).map(stripAnsiCodes)
    expect(lines.filter((line) => line === "V6 fuzz seed 31 profile mixed complete.")).toHaveLength(1)
    expect(lines.filter((line) => line === "- item 2")).toHaveLength(1)
  })
})
