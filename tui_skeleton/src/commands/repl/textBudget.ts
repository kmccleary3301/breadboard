import { stripAnsi } from "../../repl/stringUtils.js"

export interface TextBudgetResult {
  readonly text: string
  readonly truncated: boolean
}

export interface LineBudgetOptions {
  readonly omissionText?: string
}

export interface CharBudgetOptions {
  readonly ellipsis?: string
}

export interface TextBudgetOptions extends LineBudgetOptions, CharBudgetOptions {
  readonly maxLines?: number | null
  readonly maxChars?: number | null
}

export interface OneLinePreviewOptions extends CharBudgetOptions {
  readonly maxChars?: number
  readonly fallback?: string
  readonly stripAnsiCodes?: boolean
}

const CONTROL_CHARS_EXCEPT_NEWLINE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g

const normalizeLimit = (value: number | null | undefined): number | null => {
  if (value == null) return null
  if (!Number.isFinite(value)) return null
  return Math.max(0, Math.floor(value))
}

export const normalizeLineEndings = (text: string): string => text.replace(/\r\n?/g, "\n")

export const clampTextByLines = (
  text: string,
  maxLines: number | null | undefined,
  options: LineBudgetOptions = {},
): TextBudgetResult => {
  const normalized = normalizeLineEndings(text)
  const limit = normalizeLimit(maxLines)
  if (limit == null) return { text: normalized, truncated: false }
  if (limit <= 0) return { text: options.omissionText ?? "", truncated: normalized.length > 0 }

  const lines = normalized.split("\n")
  if (lines.length <= limit) return { text: normalized, truncated: false }

  const visible = lines.slice(0, limit).join("\n")
  return {
    text: `${visible}${options.omissionText ?? ""}`,
    truncated: true,
  }
}

export const clampTextByChars = (
  text: string,
  maxChars: number | null | undefined,
  options: CharBudgetOptions = {},
): TextBudgetResult => {
  const limit = normalizeLimit(maxChars)
  if (limit == null) return { text, truncated: false }
  if (text.length <= limit) return { text, truncated: false }
  if (limit <= 0) return { text: "", truncated: true }

  const ellipsis = options.ellipsis ?? "…"
  if (ellipsis.length >= limit) return { text: ellipsis.slice(0, limit), truncated: true }

  return {
    text: `${text.slice(0, limit - ellipsis.length)}${ellipsis}`,
    truncated: true,
  }
}

export const applyTextBudget = (text: string, options: TextBudgetOptions): TextBudgetResult => {
  const lineResult = clampTextByLines(text, options.maxLines, { omissionText: options.omissionText })
  const charResult = clampTextByChars(lineResult.text, options.maxChars, { ellipsis: options.ellipsis })
  return {
    text: charResult.text,
    truncated: lineResult.truncated || charResult.truncated,
  }
}

export const formatOneLinePreview = (value: string, options: OneLinePreviewOptions = {}): TextBudgetResult => {
  const stripAnsiCodes = options.stripAnsiCodes ?? true
  const fallback = options.fallback ?? "item"
  const cleaned = (stripAnsiCodes ? stripAnsi(value) : value)
    .replace(CONTROL_CHARS_EXCEPT_NEWLINE, " ")
    .replace(/\s+/g, " ")
    .trim()
  return clampTextByChars(cleaned || fallback, options.maxChars ?? 120, { ellipsis: options.ellipsis ?? "…" })
}
