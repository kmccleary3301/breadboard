import type { ThemedLine, TokenLineV1, TokenSpan } from "@stream-mdx/core/types"
import type { TuiToken, TuiTokenLine } from "../types.js"

export type TokenTheme = "dark" | "light"

export interface TokenStyleFns {
  readonly color: (text: string, color: string) => string
  readonly bold: (text: string) => string
  readonly italic: (text: string) => string
  readonly underline: (text: string) => string
}

const pickSpanStyle = (span: TokenSpan, theme: TokenTheme): { fg?: string; fs?: number } | null => {
  const themed = span.v?.[theme]
  if (themed && (themed.fg || themed.fs)) {
    return { fg: themed.fg, fs: themed.fs }
  }
  if (span.s && (span.s.fg || span.s.fs)) {
    return { fg: span.s.fg, fs: span.s.fs }
  }
  return null
}

export const tokenLineFromV1 = (line: TokenLineV1 | null | undefined, theme: TokenTheme): TuiTokenLine | null => {
  if (!line || !Array.isArray(line.spans)) return null
  const tokens: TuiToken[] = []
  for (const span of line.spans) {
    const style = pickSpanStyle(span, theme)
    tokens.push({
      content: span.t,
      color: style?.fg ?? null,
      fontStyle: style?.fs ?? null,
    })
  }
  return tokens
}

export const tokenLineFromThemed = (line: ThemedLine | null | undefined): TuiTokenLine | null => {
  if (!line || line.length === 0) return null
  return line.map((token) => ({
    content: token.content,
    color: token.color ?? null,
    fontStyle: token.fontStyle ?? null,
  }))
}

const applyFontStyle = (text: string, fontStyle: number | null | undefined, fns: TokenStyleFns): string => {
  if (!fontStyle) return text
  let styled = text
  if (fontStyle & 1) styled = fns.italic(styled)
  if (fontStyle & 2) styled = fns.bold(styled)
  if (fontStyle & 4) styled = fns.underline(styled)
  return styled
}

export const renderTokenLine = (tokens: TuiTokenLine, fns: TokenStyleFns): string => {
  return tokens
    .map((token) => {
      const base = applyFontStyle(token.content, token.fontStyle, fns)
      if (token.color) return fns.color(base, token.color)
      return base
    })
    .join("")
}
