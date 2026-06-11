import type { LiveSlotStatus } from "./types.js"
import type { TranscriptCellLifecycle, TranscriptCellRole, TranscriptItemKind, TranscriptItemSource } from "./transcriptModel.js"

export type RenderOwnershipClass =
  | "durable-transcript"
  | "live-region"
  | "overlay"
  | "footer"
  | "composer"
  | "inspector"
  | "export-only"
  | "host-boundary"
  | "unsafe-boundary"

export type RenderStabilityState =
  | "streaming"
  | "pending"
  | "finalized"
  | "frozen"
  | "collapsed"
  | "inspect-only"
  | "recovery"
  | "terminal-error"
  | "ephemeral"

export type RenderContentSafetyClass =
  | "safe-text"
  | "rendered-markdown"
  | "bounded-code"
  | "bounded-table"
  | "tool-output-preview"
  | "diff-summary"
  | "diagnostic-summary"
  | "raw-provider-detail"
  | "stack-trace"
  | "large-stdout"
  | "binary-reference"
  | "debug-payload"

export type RenderWidthPolicy = "rewrap" | "truncate" | "preserve" | "detail-only" | "collapse"

export type RenderHeightPolicy = "bounded" | "viewport-reserved" | "overlay-bounded" | "export-only"

export type RenderTruncationPolicy =
  | "none"
  | "bounded-wrap"
  | "truncate-end"
  | "truncate-middle"
  | "head-tail"
  | "collapse-detail"

export type RenderDetailPolicy = "inline-only" | "raw-copy" | "inspector" | "export" | "inspector-or-export"

export interface RenderableNodePolicy {
  readonly componentKind: TranscriptCellRole
  readonly ownershipClass: RenderOwnershipClass
  readonly stabilityState: RenderStabilityState
  readonly contentSafetyClass: RenderContentSafetyClass
  readonly widthPolicy: RenderWidthPolicy
  readonly heightPolicy: RenderHeightPolicy
  readonly truncationPolicy: RenderTruncationPolicy
  readonly detailPolicy: RenderDetailPolicy
  readonly priority: "low" | "normal" | "high" | "critical"
}

export interface TranscriptPolicyInput {
  readonly role: TranscriptCellRole
  readonly lifecycle: TranscriptCellLifecycle
  readonly kind: TranscriptItemKind
  readonly source: TranscriptItemSource
  readonly status?: LiveSlotStatus
  readonly speaker?: "assistant" | "user" | "system"
  readonly textPreview?: string
}

const durable = (role: TranscriptCellRole, overrides: Partial<RenderableNodePolicy> = {}): RenderableNodePolicy => ({
  componentKind: role,
  ownershipClass: "durable-transcript",
  stabilityState: "finalized",
  contentSafetyClass: "safe-text",
  widthPolicy: "rewrap",
  heightPolicy: "bounded",
  truncationPolicy: "bounded-wrap",
  detailPolicy: "raw-copy",
  priority: "normal",
  ...overrides,
})

const isLiveLifecycle = (lifecycle: TranscriptCellLifecycle): boolean => lifecycle === "live"

const isErrorStatus = (status: LiveSlotStatus | undefined): boolean => status === "error"

export const resolveTranscriptRenderPolicy = (input: TranscriptPolicyInput): RenderableNodePolicy => {
  const role = input.role
  const live = isLiveLifecycle(input.lifecycle)

  switch (role) {
    case "landing":
      return durable(role, {
        stabilityState: "frozen",
        widthPolicy: "preserve",
        truncationPolicy: "truncate-end",
        detailPolicy: "inline-only",
        priority: "high",
      })
    case "user-request":
      return durable(role, {
        contentSafetyClass: "safe-text",
        detailPolicy: "raw-copy",
        priority: "high",
      })
    case "assistant-message":
      return durable(role, {
        ownershipClass: live ? "live-region" : "durable-transcript",
        stabilityState: live ? "streaming" : "frozen",
        contentSafetyClass: "rendered-markdown",
        truncationPolicy: live ? "bounded-wrap" : "collapse-detail",
        detailPolicy: "raw-copy",
        priority: "high",
      })
    case "tool-call":
      return durable(role, {
        ownershipClass: live ? "live-region" : "durable-transcript",
        stabilityState: live ? "pending" : "finalized",
        contentSafetyClass: "tool-output-preview",
        widthPolicy: "truncate",
        truncationPolicy: "truncate-end",
        detailPolicy: "inspector-or-export",
        priority: "normal",
      })
    case "tool-result":
      return durable(role, {
        contentSafetyClass: "tool-output-preview",
        widthPolicy: "truncate",
        truncationPolicy: "head-tail",
        detailPolicy: "inspector-or-export",
      })
    case "tool-error":
      return durable(role, {
        stabilityState: "terminal-error",
        contentSafetyClass: "diagnostic-summary",
        widthPolicy: "truncate",
        truncationPolicy: "collapse-detail",
        detailPolicy: "inspector-or-export",
        priority: "high",
      })
    case "diff":
      return durable(role, {
        stabilityState: "collapsed",
        contentSafetyClass: "diff-summary",
        widthPolicy: "detail-only",
        truncationPolicy: "collapse-detail",
        detailPolicy: "inspector-or-export",
      })
    case "approval":
      return durable(role, {
        ownershipClass: live || input.status === "pending" ? "overlay" : "durable-transcript",
        stabilityState: live || input.status === "pending" ? "pending" : "finalized",
        widthPolicy: "truncate",
        truncationPolicy: "truncate-end",
        detailPolicy: "inspector",
        priority: "critical",
      })
    case "interrupted":
      return durable(role, {
        stabilityState: "terminal-error",
        widthPolicy: "truncate",
        truncationPolicy: "truncate-end",
        priority: "high",
      })
    case "status":
      return durable(role, {
        ownershipClass: input.lifecycle === "ephemeral" ? "footer" : "durable-transcript",
        stabilityState: input.lifecycle === "ephemeral" ? "ephemeral" : isErrorStatus(input.status) ? "terminal-error" : "finalized",
        contentSafetyClass: isErrorStatus(input.status) ? "diagnostic-summary" : "safe-text",
        widthPolicy: "truncate",
        truncationPolicy: "truncate-end",
        detailPolicy: input.lifecycle === "ephemeral" ? "inline-only" : "inspector-or-export",
      })
    case "command-result":
      return durable(role, {
        widthPolicy: "truncate",
        truncationPolicy: "head-tail",
        detailPolicy: "inspector-or-export",
      })
    case "system":
      return durable(role, {
        stabilityState: isErrorStatus(input.status) ? "terminal-error" : "finalized",
        contentSafetyClass: isErrorStatus(input.status) ? "diagnostic-summary" : "safe-text",
        widthPolicy: "truncate",
        truncationPolicy: "collapse-detail",
        detailPolicy: "inspector-or-export",
        priority: isErrorStatus(input.status) ? "high" : "normal",
      })
    case "tool-summary":
    default:
      return durable(role, {
        contentSafetyClass: "tool-output-preview",
        widthPolicy: "truncate",
        truncationPolicy: "truncate-end",
        detailPolicy: "inspector-or-export",
      })
  }
}
