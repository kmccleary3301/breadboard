export type ComposerSubmissionKind = "empty" | "slash" | "at-command" | "shell-prefix" | "prompt"

export interface ComposerSubmissionPlan {
  readonly kind: ComposerSubmissionKind
  readonly normalized: string
  readonly payload: string
  readonly shellCommand?: string
}

export const buildShellPrefixPayload = (command: string): string =>
  [
    "Run this shell command and report the result:",
    "",
    "```bash",
    command,
    "```",
  ].join("\n")

export const resolveComposerSubmission = (value: string): ComposerSubmissionPlan => {
  const trimmed = value.trim()
  const normalized = value.trimEnd()
  if (!trimmed) return { kind: "empty", normalized: "", payload: "" }
  if (trimmed.startsWith("/")) return { kind: "slash", normalized, payload: normalized }
  if (trimmed.startsWith("@")) return { kind: "at-command", normalized, payload: normalized }
  if (trimmed.startsWith("!") && trimmed.length > 1) {
    const shellCommand = trimmed.slice(1).trim()
    if (shellCommand.length > 0) {
      return {
        kind: "shell-prefix",
        normalized,
        payload: buildShellPrefixPayload(shellCommand),
        shellCommand,
      }
    }
  }
  return { kind: "prompt", normalized, payload: normalized }
}
