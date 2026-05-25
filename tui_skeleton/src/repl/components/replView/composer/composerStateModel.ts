export type ComposerPrimaryState =
  | "overlay-locked"
  | "help"
  | "file-suggesting"
  | "slash-suggesting"
  | "shell-prefix"
  | "running"
  | "multiline"
  | "typed"
  | "attachment-queued"
  | "idle-empty"

export interface ComposerStateInput {
  readonly input: string
  readonly attachmentsCount: number
  readonly fileMentionsCount: number
  readonly pendingResponse: boolean
  readonly overlayActive: boolean
  readonly shortcutsOpen: boolean
  readonly filePickerActive: boolean
  readonly suggestionsCount: number
  readonly activeSlashQuery?: string | null
  readonly phaseId?: string | null
  readonly inputLocked?: boolean
}

export interface ComposerStateModel {
  readonly primary: ComposerPrimaryState
  readonly inputVisible: boolean
  readonly placeholderEligible: boolean
  readonly footerMode: "idle" | "running" | "overlay"
  readonly hasTypedInput: boolean
  readonly hasQueuedContext: boolean
  readonly isMultiline: boolean
}

export const buildComposerStateModel = (input: ComposerStateInput): ComposerStateModel => {
  const text = input.input ?? ""
  const trimmed = text.trim()
  const hasTypedInput = text.length > 0
  const hasQueuedContext = input.attachmentsCount > 0 || input.fileMentionsCount > 0
  const isMultiline = text.includes("\n")
  const isShellPrefix = trimmed.startsWith("!") && trimmed.length > 1
  const slashSuggesting = input.suggestionsCount > 0 && (trimmed.startsWith("/") || Boolean(input.activeSlashQuery))
  const primary: ComposerPrimaryState = (() => {
    if (input.overlayActive || input.inputLocked) return "overlay-locked"
    if (input.shortcutsOpen) return "help"
    if (input.filePickerActive) return "file-suggesting"
    if (slashSuggesting) return "slash-suggesting"
    if (isShellPrefix) return "shell-prefix"
    if (input.pendingResponse) return "running"
    if (isMultiline) return "multiline"
    if (hasTypedInput) return "typed"
    if (hasQueuedContext) return "attachment-queued"
    return "idle-empty"
  })()
  return {
    primary,
    inputVisible: true,
    placeholderEligible:
      primary === "idle-empty" &&
      !input.pendingResponse &&
      !hasQueuedContext &&
      (input.phaseId == null || input.phaseId === "ready"),
    footerMode: primary === "overlay-locked" ? "overlay" : input.pendingResponse ? "running" : "idle",
    hasTypedInput,
    hasQueuedContext,
    isMultiline,
  }
}
