import { describe, expect, it } from "vitest"
import { buildTranscriptViewerDetailLabel } from "../replViewControllerUtils.js"

describe("buildTranscriptViewerDetailLabel", () => {
  it("describes follow-tail mode with search progress and export notice", () => {
    expect(
      buildTranscriptViewerDetailLabel({
        followTail: true,
        lineCount: 12,
        effectiveScroll: 8,
        bodyRows: 4,
        searchOpen: true,
        searchQuery: "hello",
        searchMatchCount: 3,
        searchSafeIndex: 1,
        verboseOutput: true,
        exportNotice: "Saved transcript.txt",
      }),
    ).toBe("follow tail · g top · search /hello 2/3 · detailed transcript · Saved transcript.txt")
  })

  it("describes inspect mode when viewer is detached from tail", () => {
    expect(
      buildTranscriptViewerDetailLabel({
        followTail: false,
        lineCount: 25,
        effectiveScroll: 5,
        bodyRows: 6,
        searchOpen: false,
        searchQuery: "",
        searchMatchCount: 0,
        searchSafeIndex: 0,
        verboseOutput: false,
        exportNotice: null,
      }),
    ).toBe("inspect 6-11/25 · G tail")
  })

  it("labels raw event viewer mode explicitly", () => {
    expect(
      buildTranscriptViewerDetailLabel({
        followTail: true,
        lineCount: 12,
        effectiveScroll: 8,
        bodyRows: 4,
        searchOpen: false,
        searchQuery: "",
        searchMatchCount: 0,
        searchSafeIndex: 0,
        verboseOutput: false,
        exportNotice: null,
        rawMode: true,
      }),
    ).toBe("raw event viewer · follow tail · g top")
  })

  it("handles empty inspect mode and empty search results", () => {
    expect(
      buildTranscriptViewerDetailLabel({
        followTail: false,
        lineCount: 0,
        effectiveScroll: 0,
        bodyRows: 5,
        searchOpen: true,
        searchQuery: "none",
        searchMatchCount: 0,
        searchSafeIndex: 0,
        verboseOutput: false,
        exportNotice: null,
      }),
    ).toBe("inspect empty · G tail · search /none 0/0")
  })
})
