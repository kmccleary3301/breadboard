import { describe, expect, it, vi } from "vitest"
import { handleTranscriptOverlayKeys } from "../handleTranscriptOverlayKeys.js"

const baseContext = () => ({
  transcriptViewerOpen: true,
  exitTranscriptViewer: vi.fn(),
  transcriptSearchMatches: [{ line: 8, start: 0, end: 4 }],
  setTranscriptSearchIndex: vi.fn(),
  jumpTranscriptToLine: vi.fn(),
  jumpTranscriptToAnchor: vi.fn(() => true),
  stdout: undefined,
  transcriptViewerFollowTail: false,
  transcriptViewerMaxScroll: 20,
  setTranscriptViewerFollowTail: vi.fn(),
  setTranscriptViewerScroll: vi.fn(),
  transcriptSearchOpen: false,
  setTranscriptSearchQuery: vi.fn(),
  setTranscriptSearchOpen: vi.fn(),
  saveTranscriptExport: vi.fn(),
  transcriptToolLines: [5],
  setTranscriptToolIndex: vi.fn(),
  transcriptViewerBodyRows: 10,
  transcriptViewerEffectiveScroll: 4,
})

const keyInfo = (seed: Partial<any> = {}) => ({
  char: undefined,
  key: {} as any,
  lowerChar: undefined,
  isReturnKey: false,
  isTabKey: false,
  isShiftTab: false,
  isCtrlT: false,
  isCtrlShiftT: false,
  isCtrlB: false,
  isCtrlY: false,
  isCtrlG: false,
  isHomeKey: false,
  isEndKey: false,
  ...seed,
})

describe("handleTranscriptOverlayKeys", () => {
  it("closes search before exiting the viewer on escape", () => {
  const context = baseContext()
  context.transcriptSearchOpen = true
    const handled = handleTranscriptOverlayKeys(context, keyInfo({ char: "\u001b", key: { escape: true } }))
    expect(handled).toBe(true)
    expect(context.setTranscriptSearchOpen).toHaveBeenCalledWith(false)
    expect(context.exitTranscriptViewer).not.toHaveBeenCalled()
  })

  it("closes the viewer on transcript toggle chords", () => {
    const context = baseContext()
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "\u000f", lowerChar: "o", key: { ctrl: true } }))).toBe(true)
    expect(context.exitTranscriptViewer).toHaveBeenCalledTimes(1)

    const context2 = baseContext()
    expect(handleTranscriptOverlayKeys(context2, keyInfo({ char: "\u0014", lowerChar: "t", key: { ctrl: true } }))).toBe(true)
    expect(context2.exitTranscriptViewer).toHaveBeenCalledTimes(1)

    const context3 = baseContext()
    expect(handleTranscriptOverlayKeys(context3, keyInfo({ char: "\u000f", key: { ctrl: true } }))).toBe(true)
    expect(context3.exitTranscriptViewer).toHaveBeenCalledTimes(1)
  })

  it("swallows command invocation residue immediately after opening the viewer", () => {
    const context = {
      ...baseContext(),
      transcriptViewerInputQuarantineUntilRef: { current: Date.now() + 1000 },
    }
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "/", lowerChar: "/", key: {} }))).toBe(true)
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "r", lowerChar: "r", key: {} }))).toBe(true)
    expect(handleTranscriptOverlayKeys(context, keyInfo({ isReturnKey: true, char: "\r", key: { return: true } }))).toBe(true)
    expect(context.setTranscriptSearchOpen).not.toHaveBeenCalled()
    expect(context.setTranscriptSearchQuery).not.toHaveBeenCalled()
    expect(context.exitTranscriptViewer).not.toHaveBeenCalled()
  })

  it("allows explicit transcript action keys immediately after opening the viewer", () => {
    const context = {
      ...baseContext(),
      resultDetailOpen: false,
      artifactPreviewOpen: false,
      selectedTranscriptToolTarget: { artifactPath: "artifacts/report.txt" },
      openSelectedTranscriptResultDetail: vi.fn(() => true),
      openSelectedTranscriptArtifactPreview: vi.fn(() => true),
      transcriptViewerInputQuarantineUntilRef: { current: Date.now() + 1000 },
    }
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "o", lowerChar: "o", key: {} }))).toBe(true)
    expect(context.openSelectedTranscriptResultDetail).toHaveBeenCalledTimes(1)
  })

  it("still lets escape exit during the viewer-open input quarantine", () => {
    const context = {
      ...baseContext(),
      transcriptViewerInputQuarantineUntilRef: { current: Date.now() + 1000 },
    }
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "\u001b", key: { escape: true } }))).toBe(true)
    expect(context.exitTranscriptViewer).toHaveBeenCalledTimes(1)
  })

  it("routes anchor keys through the anchor jumper", () => {
    const context = baseContext()
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "a", lowerChar: "a", key: {} }))).toBe(true)
    expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("assistant")
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "u", lowerChar: "u", key: {} }))).toBe(true)
    expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("user")
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "e", lowerChar: "e", key: {} }))).toBe(true)
    expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("error")
  expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "w", lowerChar: "w", key: {} }))).toBe(true)
  expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("warning")
  })

  it("handles printable transcript keys even when lowerChar is absent", () => {
    const context = baseContext()
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "/", key: {} }))).toBe(true)
    expect(context.setTranscriptSearchOpen).toHaveBeenCalled()

    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "u", key: {} }))).toBe(true)
    expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("user")

    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "G", key: {} }))).toBe(true)
    expect(context.setTranscriptViewerFollowTail).toHaveBeenCalledWith(true)
  })

  it("adds printable search characters when lowerChar is absent", () => {
    const context = baseContext()
    context.transcriptSearchOpen = true
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "e", key: {} }))).toBe(true)
    expect(context.setTranscriptSearchQuery).toHaveBeenCalled()
  })

  it("opens result detail and artifact preview from transcript tool selection", () => {
    const context = {
      ...baseContext(),
      resultDetailOpen: false,
      artifactPreviewOpen: false,
      selectedTranscriptToolTarget: { artifactPath: "artifacts/report.txt" },
      openSelectedTranscriptResultDetail: vi.fn(() => true),
      openSelectedTranscriptArtifactPreview: vi.fn(() => true),
    }
    expect(handleTranscriptOverlayKeys(context, keyInfo({ char: "o", lowerChar: "o", key: {} }))).toBe(true)
    expect(context.openSelectedTranscriptResultDetail).toHaveBeenCalled()
    expect(handleTranscriptOverlayKeys(context, keyInfo({ isReturnKey: true, key: { return: true } }))).toBe(true)
    expect(context.openSelectedTranscriptArtifactPreview).toHaveBeenCalled()
  })

  it("yields transcript control when result detail or artifact preview is already open", () => {
    expect(handleTranscriptOverlayKeys({ ...baseContext(), resultDetailOpen: true }, keyInfo({ char: "o", lowerChar: "o", key: {} }))).toBeUndefined()
    expect(handleTranscriptOverlayKeys({ ...baseContext(), artifactPreviewOpen: true }, keyInfo({ char: "o", lowerChar: "o", key: {} }))).toBeUndefined()
  })
})
