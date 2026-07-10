import { describe, expect, it } from "vitest"
import { buildManagedViewportResetKey, computeManagedBodyRows, shouldAppendHistoryLanding, shouldShowInlineSessionHeader } from "../scrollbackViewportPolicy.js"

describe("scrollbackViewportPolicy", () => {
  describe("shouldAppendHistoryLanding", () => {
    it("appends compact landing only when scrollback mode needs a fallback header", () => {
      expect(
        shouldAppendHistoryLanding({
          scrollbackMode: true,
          hasLandingNode: true,
          landingRetired: true,
          landingShouldRetireNow: false,
        }),
      ).toBe(true)
    })

    it("still appends a frozen landing after an inline landing has been retired", () => {
      expect(
        shouldAppendHistoryLanding({
          scrollbackMode: true,
          hasLandingNode: true,
          landingRetired: true,
          landingShouldRetireNow: true,
        }),
      ).toBe(true)
    })

    it("does not append anything while landing remains inline and unretired", () => {
      expect(
        shouldAppendHistoryLanding({
          scrollbackMode: true,
          hasLandingNode: true,
          landingRetired: false,
          landingShouldRetireNow: false,
        }),
      ).toBe(false)
    })

    it("does not append anything outside scrollback mode or without a landing node", () => {
      expect(
        shouldAppendHistoryLanding({
          scrollbackMode: false,
          hasLandingNode: true,
          landingRetired: true,
          landingShouldRetireNow: true,
        }),
      ).toBe(false)
      expect(
        shouldAppendHistoryLanding({
          scrollbackMode: true,
          hasLandingNode: false,
          landingRetired: true,
          landingShouldRetireNow: true,
        }),
      ).toBe(false)
    })
  })

  describe("shouldShowInlineSessionHeader", () => {
    it("shows the compact session header after rich landing retirement", () => {
      expect(
        shouldShowInlineSessionHeader({
          scrollbackMode: true,
          hasSessionHeaderNode: true,
          landingRetired: true,
          warmLandingVisible: false,
        }),
      ).toBe(true)
    })

    it("does not show the compact session header while landing remains warm inline", () => {
      expect(
        shouldShowInlineSessionHeader({
          scrollbackMode: true,
          hasSessionHeaderNode: true,
          landingRetired: false,
          warmLandingVisible: true,
        }),
      ).toBe(false)
    })



    it("suppresses the compact session header when it would displace the active region", () => {
      expect(
        shouldShowInlineSessionHeader({
          scrollbackMode: true,
          hasSessionHeaderNode: true,
          landingRetired: true,
          warmLandingVisible: false,
          fitsActiveRegion: false,
        }),
      ).toBe(false)
    })

    it("does not show the compact session header outside scrollback mode or without a node", () => {
      expect(
        shouldShowInlineSessionHeader({
          scrollbackMode: false,
          hasSessionHeaderNode: true,
          landingRetired: true,
          warmLandingVisible: false,
        }),
      ).toBe(false)
      expect(
        shouldShowInlineSessionHeader({
          scrollbackMode: true,
          hasSessionHeaderNode: false,
          landingRetired: true,
          warmLandingVisible: false,
        }),
      ).toBe(false)
    })
  })


  describe("computeManagedBodyRows", () => {
    it("excludes static feed rows from preserved-scrollback active-region reclaim", () => {
      expect(
        computeManagedBodyRows({
          scrollbackMode: true,
          ownedSceneMode: false,
          staticFeedLineCount: 80,
          guardrailRows: 0,
          networkBannerRows: 0,
          landingRows: 4,
          sessionHeaderRows: 0,
          ownedSceneHostRows: 1,
          transcriptOrViewerRows: 12,
          liveSlotRows: 0,
          collapsedHintRows: 0,
          overlayRows: 0,
        }),
      ).toBe(16)
    })

    it("still includes active chrome, transcript, and live rows in preserved-scrollback reclaim", () => {
      expect(
        computeManagedBodyRows({
          scrollbackMode: true,
          ownedSceneMode: false,
          staticFeedLineCount: 50,
          guardrailRows: 6,
          networkBannerRows: 7,
          landingRows: 0,
          sessionHeaderRows: 3,
          ownedSceneHostRows: 1,
          transcriptOrViewerRows: 10,
          liveSlotRows: 2,
          collapsedHintRows: 2,
          overlayRows: 0,
        }),
      ).toBe(30)
    })

    it("includes overlay rows in preserved-scrollback active-region reclaim", () => {
      expect(
        computeManagedBodyRows({
          scrollbackMode: true,
          ownedSceneMode: false,
          staticFeedLineCount: 50,
          guardrailRows: 0,
          networkBannerRows: 0,
          landingRows: 0,
          sessionHeaderRows: 0,
          ownedSceneHostRows: 0,
          transcriptOrViewerRows: 0,
          liveSlotRows: 0,
          collapsedHintRows: 0,
          overlayRows: 14,
        }),
      ).toBe(14)
    })
  })

  describe("buildManagedViewportResetKey", () => {
    it("stays stable when run-state inputs are stable", () => {
      const first = buildManagedViewportResetKey({
        sessionId: "session-1",
        viewClearAt: null,
        networkBannerTone: null,
        hasGuardrailNotice: false,
        runStateEpoch: "idle|settled|turn:1",
      })
      const second = buildManagedViewportResetKey({
        sessionId: "session-1",
        viewClearAt: null,
        networkBannerTone: null,
        hasGuardrailNotice: false,
        runStateEpoch: "idle|settled|turn:1",
      })
      expect(second).toBe(first)
    })

    it("changes on explicit clear, chrome changes, and turn-boundary run-state changes", () => {
      const base = buildManagedViewportResetKey({
        sessionId: "session-1",
        viewClearAt: null,
        networkBannerTone: null,
        hasGuardrailNotice: false,
        runStateEpoch: "idle|settled|turn:1",
      })
      expect(
        buildManagedViewportResetKey({
          sessionId: "session-1",
          viewClearAt: 123,
          networkBannerTone: null,
          hasGuardrailNotice: false,
          runStateEpoch: "idle|settled|turn:1",
        }),
      ).not.toBe(base)
      expect(
        buildManagedViewportResetKey({
          sessionId: "session-1",
          viewClearAt: null,
          networkBannerTone: "warning",
          hasGuardrailNotice: false,
          runStateEpoch: "idle|settled|turn:1",
        }),
      ).not.toBe(base)
      expect(
        buildManagedViewportResetKey({
          sessionId: "session-1",
          viewClearAt: null,
          networkBannerTone: null,
          hasGuardrailNotice: true,
          runStateEpoch: "idle|settled|turn:1",
        }),
      ).not.toBe(base)
      expect(
        buildManagedViewportResetKey({
          sessionId: "session-1",
          viewClearAt: null,
          networkBannerTone: null,
          hasGuardrailNotice: false,
          runStateEpoch: "active|tail|turn:1",
        }),
      ).not.toBe(base)
      expect(
        buildManagedViewportResetKey({
          sessionId: "session-1",
          viewClearAt: null,
          networkBannerTone: null,
          hasGuardrailNotice: false,
          runStateEpoch: "idle|settled|turn:2",
        }),
      ).not.toBe(base)
    })
  })
})
