import { describe, expect, it } from "vitest"
import { resolveEmptyInputRemovalDecision, resolveSlashEnterDecision } from "../useEditorKeys.js"

describe("resolveSlashEnterDecision", () => {
  it("allows exact commands with arguments to reach command argument validation", () => {
    expect(
      resolveSlashEnterDecision({
        input: "/help extra",
        suggestions: [{ command: "/help", availability: "available" }],
        suggestIndex: 0,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "allow-submit-exact" })
  })

  it("still applies suggestions for non-exact partial commands", () => {
    expect(
      resolveSlashEnterDecision({
        input: "/hel",
        suggestions: [{ command: "/help", availability: "available" }],
        suggestIndex: 0,
        hasSubmitSuggestedSlashCommand: true,
      }),
    ).toEqual({ kind: "submit-suggestion", command: "/help" })
  })
})

describe("resolveEmptyInputRemovalDecision", () => {
  it("removes image attachments before queued file mentions on empty-input Backspace", () => {
    expect(
      resolveEmptyInputRemovalDecision({
        attachmentCount: 1,
        fileMentionCount: 1,
        inputLength: 0,
        cursor: 0,
        overlayActive: false,
        isBackspace: true,
      }),
    ).toEqual({ kind: "attachment" })
  })

  it("removes queued file mentions when no image attachments remain", () => {
    expect(
      resolveEmptyInputRemovalDecision({
        attachmentCount: 0,
        fileMentionCount: 1,
        inputLength: 0,
        cursor: 0,
        overlayActive: false,
        isBackspace: true,
      }),
    ).toEqual({ kind: "file-mention" })
  })

  it("does not remove queued context while an overlay is active or text remains", () => {
    expect(
      resolveEmptyInputRemovalDecision({
        attachmentCount: 0,
        fileMentionCount: 1,
        inputLength: 3,
        cursor: 3,
        overlayActive: false,
        isBackspace: true,
      }),
    ).toEqual({ kind: "none" })
    expect(
      resolveEmptyInputRemovalDecision({
        attachmentCount: 0,
        fileMentionCount: 1,
        inputLength: 0,
        cursor: 0,
        overlayActive: true,
        isBackspace: true,
      }),
    ).toEqual({ kind: "none" })
  })
})
