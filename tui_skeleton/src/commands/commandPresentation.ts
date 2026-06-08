import { Console } from "effect"
import { printJsonOutput } from "./commandOutput.js"

export type CommandPresentationMode = "text" | "table" | "json"

export interface ResolvedCommandPresentation {
  readonly kind: "json" | "text" | "none"
  readonly value: string
}

export const resolveCommandPresentation = (options: {
  readonly mode: CommandPresentationMode
  readonly jsonValue: unknown
  readonly text?: string | null
}): ResolvedCommandPresentation => {
  if (options.mode === "json") {
    return {
      kind: "json",
      value: JSON.stringify(options.jsonValue, null, 2),
    }
  }
  const text = options.text ?? ""
  if (text.length === 0) {
    return { kind: "none", value: "" }
  }
  return {
    kind: "text",
    value: text,
  }
}

export const printCommandPresentation = async (options: {
  readonly mode: CommandPresentationMode
  readonly jsonValue: unknown
  readonly text?: string | null
}): Promise<void> => {
  const resolved = resolveCommandPresentation(options)
  if (resolved.kind === "json") {
    await printJsonOutput(options.jsonValue)
    return
  }
  if (resolved.kind === "text") {
    await Console.log(resolved.value)
  }
}

export const renderCompletionBlock = (completion: unknown): string => `\n---\nCompletion: ${JSON.stringify(completion)}`
