import chalk from "chalk"
import type { Highlighter } from "shiki"

const SHIKI_THEME = "andromeeda"
const MAX_SHIKI_LINES = 500
const MAX_SHIKI_CHARS = 120_000
const DEFAULT_LANGS = [
  "ts",
  "tsx",
  "js",
  "jsx",
  "json",
  "bash",
  "sh",
  "zsh",
  "python",
  "py",
  "go",
  "rust",
  "java",
  "c",
  "cpp",
  "diff",
  "yaml",
  "yml",
  "markdown",
  "md",
  "html",
  "css",
  "xml",
]

type ShikiToken = {
  readonly content: string
  readonly color?: string
  readonly fontStyle?: number
}

type ShikiStatus = "idle" | "loading" | "ready" | "failed"
type ShikiLoadLanguage = Parameters<Highlighter["loadLanguage"]>[0]
type CodeToTokensOptions = NonNullable<Parameters<Highlighter["codeToTokens"]>[1]>
type ShikiLang = CodeToTokensOptions["lang"]

let status: ShikiStatus = "idle"
let highlighter: Highlighter | null = null
let loadPromise: Promise<Highlighter> | null = null
let lastError: string | null = null
const listeners = new Set<() => void>()
const languageLoads = new Map<string, Promise<void>>()

const notify = () => {
  for (const listener of listeners) {
    listener()
  }
}

export const subscribeShiki = (listener: () => void): (() => void) => {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export const getShikiStatus = (): { status: ShikiStatus; error: string | null } => ({
  status,
  error: lastError,
})

export const ensureShikiLoaded = (): void => {
  if (status !== "idle") return
  status = "loading"
  loadPromise = import("shiki")
    .then(async ({ createHighlighter }) => {
      const loaded = await createHighlighter({
        themes: [SHIKI_THEME],
        langs: DEFAULT_LANGS,
      })
      return loaded
    })
    .then((loaded) => {
      highlighter = loaded
      status = "ready"
      notify()
      return loaded
    })
    .catch((err) => {
      status = "failed"
      lastError = err instanceof Error ? err.message : String(err)
      notify()
      throw err
    })
}

const normalizeLang = (lang?: string): string | null => {
  if (!lang) return null
  const trimmed = lang.trim()
  if (!trimmed) return null
  const [first] = trimmed.split(/[\s{]/)
  const normalized = first?.toLowerCase() ?? ""
  if (!normalized) return null
  if (normalized === "text" || normalized === "plain") return null
  return normalized
}

const requestLanguage = (lang: ShikiLoadLanguage): void => {
  if (!highlighter) return
  if (typeof lang !== "string") return
  if (highlighter.getLanguage(lang)) return
  if (languageLoads.has(lang)) return
  const promise = highlighter
    .loadLanguage(lang)
    .then(() => {
      languageLoads.delete(lang)
      notify()
    })
    .catch(() => {
      languageLoads.delete(lang)
      notify()
    })
  languageLoads.set(lang, promise)
}

const applyTokenStyle = (token: ShikiToken): string => {
  let styler = token.color ? chalk.hex(token.color) : chalk
  const fontStyle = token.fontStyle ?? 0
  if (fontStyle & 1) styler = styler.italic
  if (fontStyle & 2) styler = styler.bold
  if (fontStyle & 4) styler = styler.underline
  return styler(token.content)
}

const tokensToAnsiLines = (tokenLines: ShikiToken[][]): string[] =>
  tokenLines.map((line) => line.map((token) => applyTokenStyle(token)).join(""))

export const maybeHighlightCode = (code: string, lang?: string): string[] | null => {
  const normalized = normalizeLang(lang)
  if (!normalized) return null
  if (code.length > MAX_SHIKI_CHARS) return null
  const lines = code.split(/\r?\n/)
  if (lines.length > MAX_SHIKI_LINES) return null

  if (status === "idle") {
    ensureShikiLoaded()
    return null
  }

  if (!highlighter) return null

  const resolved = highlighter.resolveLangAlias(normalized)
  const langId = resolved ?? normalized
  if (!highlighter.getLanguage(langId)) {
    requestLanguage(langId as ShikiLoadLanguage)
    return null
  }

  const result = highlighter.codeToTokens(code, { lang: langId as ShikiLang, theme: SHIKI_THEME })
  return tokensToAnsiLines(result.tokens as ShikiToken[][])
}
