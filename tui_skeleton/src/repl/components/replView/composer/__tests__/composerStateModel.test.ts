import { describe, expect, it } from "vitest"
import { buildComposerStateModel } from "../composerStateModel.js"

const base = (overrides: Partial<Parameters<typeof buildComposerStateModel>[0]> = {}) =>
  buildComposerStateModel({
    input: "",
    attachmentsCount: 0,
    fileMentionsCount: 0,
    pendingResponse: false,
    overlayActive: false,
    shortcutsOpen: false,
    filePickerActive: false,
    suggestionsCount: 0,
    activeSlashQuery: null,
    phaseId: "ready",
    ...overrides,
  })

describe("buildComposerStateModel", () => {
  it("keeps idle empty composer visible and placeholder-eligible", () => {
    expect(base()).toMatchObject({
      primary: "idle-empty",
      inputVisible: true,
      placeholderEligible: true,
      footerMode: "idle",
    })
  })

  it("classifies typed and multiline input before queued attachment-only state", () => {
    expect(base({ input: "hello" }).primary).toBe("typed")
    expect(base({ input: "hello\nworld" })).toMatchObject({ primary: "multiline", isMultiline: true })
    expect(base({ attachmentsCount: 1 })).toMatchObject({ primary: "attachment-queued", hasQueuedContext: true })
  })

  it("gives help, file, slash, running, and overlay states explicit priority", () => {
    expect(base({ shortcutsOpen: true, input: "abc" }).primary).toBe("help")
    expect(base({ filePickerActive: true, input: "@src" }).primary).toBe("file-suggesting")
    expect(base({ suggestionsCount: 2, input: "/mo", activeSlashQuery: "mo" }).primary).toBe("slash-suggesting")
    expect(base({ input: "!pwd" }).primary).toBe("shell-prefix")
    expect(base({ pendingResponse: true, input: "" })).toMatchObject({ primary: "running", footerMode: "running" })
    expect(base({ overlayActive: true, pendingResponse: true })).toMatchObject({ primary: "overlay-locked", footerMode: "overlay" })
  })

  it("suppresses placeholder eligibility outside true ready idle", () => {
    expect(base({ pendingResponse: true }).placeholderEligible).toBe(false)
    expect(base({ attachmentsCount: 1 }).placeholderEligible).toBe(false)
    expect(base({ input: "typed" }).placeholderEligible).toBe(false)
    expect(base({ phaseId: "responding" }).placeholderEligible).toBe(false)
  })
})
