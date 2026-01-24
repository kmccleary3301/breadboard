import type { Block, InlineNode } from "@stream-mdx/core/types"
import { maybeHighlightCode } from "../../../../shikiHighlighter.js"
import { CHALK, COLORS, GLYPHS } from "../../theme.js"
import { stripAnsiCodes } from "../../utils/ansi.js"

export const stripFence = (raw: string): { code: string; langHint?: string } => {
  const normalized = raw.replace(/\r\n?/g, "\n")
  const lines = normalized.split("\n")
  if (lines.length === 0) return { code: raw }
  const first = lines[0].trim()
  if (!first.startsWith("```")) return { code: normalized }
  const langHint = first.slice(3).trim() || undefined
  let end = lines.length - 1
  while (end > 0 && lines[end].trim().length === 0) end -= 1
  if (end > 0 && lines[end].trim().startsWith("```")) end -= 1
  return { code: lines.slice(1, end + 1).join("\n"), langHint }
}

export const formatInlineNodes = (nodes?: ReadonlyArray<InlineNode>): string => {
  if (!nodes || nodes.length === 0) return ""
  const render = (node: InlineNode): string => {
    switch (node.kind) {
      case "text":
        return node.text
      case "strong":
        return CHALK.bold(formatInlineNodes(node.children))
      case "em":
        return CHALK.italic(formatInlineNodes(node.children))
      case "strike":
        return CHALK.strikethrough(formatInlineNodes(node.children))
      case "code":
        return CHALK.bgHex(COLORS.panel).hex(COLORS.textBright)(` ${node.text} `)
      case "link":
        return `${CHALK.underline(formatInlineNodes(node.children))}${node.href ? CHALK.dim(` (${node.href})`) : ""}`
      case "mention":
        return CHALK.hex(COLORS.info)(`@${node.handle}`)
      case "citation":
        return CHALK.hex(COLORS.info)(`[${node.id}]`)
      case "math-inline":
      case "math-display":
        return CHALK.hex(COLORS.info)(node.tex)
      case "footnote-ref":
        return CHALK.hex(COLORS.accent)(`[^${node.label}]`)
      case "image":
        return node.alt ? `![${node.alt}]` : "[image]"
      case "br":
        return "\n"
      default:
        return "children" in node ? formatInlineNodes((node as { children?: InlineNode[] }).children) : ""
    }
  }
  return nodes.map(render).join("")
}

const colorDiffLine = (line: string): string => {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return CHALK.hex(COLORS.info)(line)
  if (line.startsWith("@@")) return CHALK.hex(COLORS.accent)(line)
  if (line.startsWith("+")) return CHALK.bgHex(COLORS.panel).hex(COLORS.success)(line)
  if (line.startsWith("-")) return CHALK.bgHex(COLORS.panel).hex(COLORS.error)(line)
  return line
}

const looksLikeDiffBlock = (lines: ReadonlyArray<string>): boolean =>
  lines.some(
    (line) =>
      line.startsWith("diff --git") ||
      line.startsWith("index ") ||
      line.startsWith("@@") ||
      line.startsWith("+++ ") ||
      line.startsWith("--- "),
  )

export const renderCodeLines = (raw: string, lang?: string): string[] => {
  const { code, langHint } = stripFence(raw)
  const finalLang = lang ?? langHint
  const content = (code || raw).replace(/\r\n?/g, "\n")
  const isDiff = finalLang ? finalLang.toLowerCase().includes("diff") : false
  const shikiLines = maybeHighlightCode(content, finalLang)
  if (shikiLines) return shikiLines
  const lines = content.split("\n")
  return lines.map((line) => {
    if (isDiff) return colorDiffLine(line)
    if (line.startsWith("+")) return CHALK.hex(COLORS.success)(line)
    if (line.startsWith("-")) return CHALK.hex(COLORS.error)(line)
    if (line.startsWith("@@")) return CHALK.hex(COLORS.info)(line)
    return CHALK.hex(COLORS.text)(line)
  })
}

const renderTableLines = (raw: string): string[] => {
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  if (lines.length === 0) return []
  const rows = lines.map((line) => {
    const parts = line.split("|").map((part) => part.trim())
    if (parts.length > 1 && parts[0] === "") parts.shift()
    if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop()
    return parts
  })
  const isSeparator = (cells: string[]) => cells.every((cell) => /^:?-{2,}:?$/.test(cell))
  const bodyRows = rows.filter((cells) => !isSeparator(cells))
  if (bodyRows.length === 0) return lines
  const widths = new Array(bodyRows[0]?.length ?? 0).fill(0)
  for (const cells of bodyRows) {
    for (let i = 0; i < widths.length; i += 1) {
      widths[i] = Math.max(widths[i], stripAnsiCodes(cells[i] ?? "").length)
    }
  }
  const formatRow = (cells: string[], style?: (value: string) => string): string => {
    const padded = cells.map((rawCell, idx) => {
      const cell = rawCell ?? ""
      const pad = Math.max(0, widths[idx] - stripAnsiCodes(rawCell).length)
      const text = `${cell}${" ".repeat(pad)}`
      return style ? style(text) : text
    })
    return `| ${padded.join(" | ")} |`
  }
  const output: string[] = []
  let headerRendered = false
  for (const cells of rows) {
    if (isSeparator(cells)) {
      const divider = widths.map((width) => "-".repeat(Math.max(3, width))).join("-|-" )
      output.push(CHALK.dim(`|- ${divider} -|`.replace(/\s+/g, " ")))
      headerRendered = true
      continue
    }
    const styled = !headerRendered ? (value: string) => CHALK.bold.hex(COLORS.textBright)(value) : undefined
    output.push(formatRow(cells, styled))
  }
  return output.length > 0 ? output : lines
}

const blockToLines = (block: Block): string[] => {
  const meta = (block.payload?.meta ?? {}) as Record<string, unknown>
  switch (block.type) {
    case "paragraph": {
      const value = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      const lines = value.split(/\r?\n/)
      return looksLikeDiffBlock(lines) ? lines.map((line) => colorDiffLine(line)) : lines
    }
    case "heading": {
      const levelRaw = typeof meta.level === "number" ? meta.level : typeof meta.depth === "number" ? meta.depth : 1
      const level = Math.min(6, Math.max(1, levelRaw || 1))
      const prefix = "#".repeat(level)
      const text = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      return [CHALK.bold.hex(COLORS.accent)(`${prefix} ${text}`)]
    }
    case "blockquote": {
      const content = block.payload.inline ? formatInlineNodes(block.payload.inline) : block.payload.raw ?? ""
      return content.split(/\r?\n/).map((line) => CHALK.hex(COLORS.muted)(`> ${line}`))
    }
    case "code": {
      const lang =
        typeof meta.lang === "string"
          ? meta.lang
          : typeof meta.language === "string"
            ? meta.language
            : typeof meta.info === "string"
              ? meta.info
              : undefined
      const raw = block.payload.raw ?? ""
      return renderCodeLines(raw, lang)
    }
    case "list": {
      const raw = block.payload.raw ?? ""
      return raw.split(/\r?\n/).map((line) => CHALK.hex(COLORS.textBright)(line))
    }
    case "footnotes":
    case "footnote-def":
      return (block.payload.raw ?? "").split(/\r?\n/)
    case "table": {
      const raw = block.payload.raw ?? ""
      return renderTableLines(raw)
    }
    case "mdx":
    case "html": {
      const raw = block.payload.raw ?? ""
      const lines = raw.split(/\r?\n/)
      return lines.map((line) => CHALK.dim(line))
    }
    default:
      return (block.payload.raw ?? "").split(/\r?\n/)
  }
}

export const blocksToLines = (blocks?: ReadonlyArray<Block>): string[] => {
  if (!blocks || blocks.length === 0) return []
  const lines: string[] = []
  for (const block of blocks) {
    const rendered = blockToLines(block)
    if (rendered.length === 0) continue
    lines.push(...rendered)
  }
  return lines
}
