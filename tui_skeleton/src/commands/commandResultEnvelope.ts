import { Console } from "effect"
import { printCommandPresentation, type CommandPresentationMode, type ResolvedCommandPresentation, resolveCommandPresentation } from "./commandPresentation.js"

export type CommandResultEnvelopeMode = CommandPresentationMode | "yaml" | "summary"

export interface CommandResultEnvelope<T> {
  readonly jsonValue: T
  readonly text: string
  readonly yamlText?: string | null
}

export interface ResolvedCommandResultEnvelope {
  readonly kind: "json" | "text" | "yaml" | "none"
  readonly value: string
}

export const resolveCommandResultEnvelope = <T>(options: {
  readonly mode: CommandResultEnvelopeMode
  readonly envelope: CommandResultEnvelope<T>
}): ResolvedCommandResultEnvelope => {
  if (options.mode === "yaml") {
    return { kind: "yaml", value: options.envelope.yamlText ?? "" }
  }
  if (options.mode === "summary") {
    return options.envelope.text.length > 0
      ? { kind: "text", value: options.envelope.text }
      : { kind: "none", value: "" }
  }
  const resolved: ResolvedCommandPresentation = resolveCommandPresentation({
    mode: options.mode,
    jsonValue: options.envelope.jsonValue,
    text: options.envelope.text,
  })
  return resolved.kind === "none" ? { kind: "none", value: "" } : resolved
}

export const printCommandResultEnvelope = async <T>(options: {
  readonly mode: CommandResultEnvelopeMode
  readonly envelope: CommandResultEnvelope<T>
}): Promise<void> => {
  const resolved = resolveCommandResultEnvelope(options)
  if (resolved.kind === "yaml") {
    await Console.log(resolved.value)
    return
  }
  if (options.mode === "summary") {
    if (resolved.kind === "text") {
      await Console.log(resolved.value)
    }
    return
  }
  const presentationMode: CommandPresentationMode = options.mode === "json" ? "json" : options.mode === "table" ? "table" : "text"
  await printCommandPresentation({
    mode: presentationMode,
    jsonValue: options.envelope.jsonValue,
    text: options.envelope.text,
  })
}
