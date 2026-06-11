import type { ConversationEntry, ToolLogKind } from "../../repl/types.js"

export type DurableTranscriptSurface =
  | { readonly surface: "conversation"; readonly speaker: ConversationEntry["speaker"] }
  | { readonly surface: "tool"; readonly kind: ToolLogKind }

export interface DurableTranscriptSafetyIssue {
  readonly code: "raw_provider_payload" | "raw_provider_error_code" | "raw_provider_type"
  readonly message: string
}

const RAW_PROVIDER_PAYLOAD_PATTERNS: ReadonlyArray<{
  readonly code: DurableTranscriptSafetyIssue["code"]
  readonly pattern: RegExp
  readonly message: string
}> = [
  {
    code: "raw_provider_payload",
    pattern: /\{['"]error['"]\s*:\s*\{|\{\\"error\\"\s*:\s*\{/i,
    message: "raw provider error object leaked into a durable transcript row",
  },
  {
    code: "raw_provider_error_code",
    pattern: /\bError code:\s*(?:4\d\d|5\d\d)\s*-\s*\{['"]error['"]\s*:/i,
    message: "provider error code plus raw error payload leaked into a durable transcript row",
  },
  {
    code: "raw_provider_type",
    pattern: /['"]type['"]\s*:\s*['"](?:invalid_request_error|insufficient_quota|rate_limit_error|authentication_error)['"]/i,
    message: "raw provider error type leaked into a durable transcript row",
  },
]

const scopedToDiagnosticSurface = (surface: DurableTranscriptSurface): boolean => {
  if (surface.surface === "conversation") return surface.speaker === "system"
  return surface.kind === "status" || surface.kind === "error" || surface.kind === "completion"
}

export const findDurableTranscriptSafetyIssues = (
  text: string,
  surface: DurableTranscriptSurface,
): DurableTranscriptSafetyIssue[] => {
  if (!scopedToDiagnosticSurface(surface)) return []
  return RAW_PROVIDER_PAYLOAD_PATTERNS
    .filter((candidate) => candidate.pattern.test(text))
    .map((candidate) => ({ code: candidate.code, message: candidate.message }))
}

export const assertDurableTranscriptSafe = (
  text: string,
  surface: DurableTranscriptSurface,
  context: string,
): void => {
  if (process.env.BREADBOARD_TUI_STRICT_TRANSCRIPT_SAFETY !== "1") return
  const issues = findDurableTranscriptSafetyIssues(text, surface)
  if (issues.length === 0) return
  const issueSummary = issues.map((issue) => issue.code).join(", ")
  throw new Error(`Unsafe durable transcript row (${context}): ${issueSummary}`)
}
