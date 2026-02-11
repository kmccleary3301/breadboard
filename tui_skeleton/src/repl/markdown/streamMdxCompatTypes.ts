export type DiffKind = "meta" | "hunk" | "add" | "del" | "context" | string

export interface TokenSpanStyle {
  readonly fg?: string
  readonly fs?: number
}

export interface TokenSpan {
  readonly t: string
  readonly s?: TokenSpanStyle
  readonly v?: {
    readonly dark?: TokenSpanStyle
    readonly light?: TokenSpanStyle
  }
}

export interface TokenLineV1 {
  readonly spans: ReadonlyArray<TokenSpan>
}

export type ThemedLine = ReadonlyArray<{
  readonly content: string
  readonly color?: string
  readonly fontStyle?: number
}>

export interface DiffLineV1 {
  readonly kind?: DiffKind | null
  readonly marker?: string
  readonly raw?: string
  readonly text?: string
  readonly oldNo?: number | null
  readonly newNo?: number | null
  readonly tokens?: TokenLineV1 | ThemedLine | null
  readonly diffKind?: DiffKind | null
}

export interface DiffBlock {
  readonly filePath?: string | null
  readonly language?: string | null
  readonly lines?: ReadonlyArray<DiffLineV1>
}
