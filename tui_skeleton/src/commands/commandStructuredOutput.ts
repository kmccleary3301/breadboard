export type StructuredOutputMode = "json" | "yaml" | "summary"

export interface StructuredOutputOptions {
  readonly mode: StructuredOutputMode
  readonly jsonValue: unknown
  readonly summaryText: string
  readonly yamlText?: string | null
}

export const resolveStructuredOutput = (options: StructuredOutputOptions): string => {
  if (options.mode === "json") {
    return JSON.stringify(options.jsonValue, null, 2)
  }
  if (options.mode === "yaml") {
    return options.yamlText ?? ""
  }
  return options.summaryText
}
