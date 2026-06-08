import { describe, expect, it } from "vitest"
import { hasWarmLandingMeaningfulInteraction, resolveLandingLifecycle } from "../landingLifecycle.js"

describe("hasWarmLandingMeaningfulInteraction", () => {
  it("does not retire warm landing for startup metadata churn alone", () => {
    expect(
      hasWarmLandingMeaningfulInteraction({
        pendingResponse: false,
        conversationCount: 0,
        transcriptCommittedCount: 0,
        transcriptTailCount: 0,
      }),
    ).toBe(false)
  })

  it("retires warm landing once the session has live activity", () => {
    expect(
      hasWarmLandingMeaningfulInteraction({
        pendingResponse: true,
        conversationCount: 0,
        transcriptCommittedCount: 0,
        transcriptTailCount: 0,
      }),
    ).toBe(true)
  })

  it("retires warm landing once transcript-visible content exists", () => {
    expect(
      hasWarmLandingMeaningfulInteraction({
        pendingResponse: false,
        conversationCount: 1,
        transcriptCommittedCount: 0,
        transcriptTailCount: 0,
      }),
    ).toBe(true)

    expect(
      hasWarmLandingMeaningfulInteraction({
        pendingResponse: false,
        conversationCount: 0,
        transcriptCommittedCount: 1,
        transcriptTailCount: 0,
      }),
    ).toBe(true)

    expect(
      hasWarmLandingMeaningfulInteraction({
        pendingResponse: false,
        conversationCount: 0,
        transcriptCommittedCount: 0,
        transcriptTailCount: 1,
      }),
    ).toBe(true)
  })
})

describe("resolveLandingLifecycle", () => {
  const base = {
    scrollbackMode: true,
    hasLandingNode: true,
    landingVariant: "board",
    landingRows: 12,
    bodyBudgetRows: 30,
    warmLandingVisible: true,
    landingRetired: false,
    landingShouldRetireNow: false,
    appendLandingToFeed: false,
    landingAlways: false,
    transcriptCommittedCount: 0,
    transcriptTailCount: 0,
    pendingResponse: false,
  }

  it("records rich fresh landing visibility explicitly", () => {
    expect(resolveLandingLifecycle(base)).toMatchObject({
      state: "fresh-visible",
      visibleInline: true,
      committedSnapshot: false,
    })
  })

  it("records compact landing visibility explicitly", () => {
    expect(resolveLandingLifecycle({ ...base, landingVariant: "compact" })).toMatchObject({
      state: "compact-visible",
      visibleInline: true,
    })
  })

  it("records committed snapshot when the warm landing retires into the static feed", () => {
    expect(
      resolveLandingLifecycle({
        ...base,
        warmLandingVisible: false,
        landingShouldRetireNow: true,
        appendLandingToFeed: true,
        transcriptCommittedCount: 2,
      }),
    ).toMatchObject({
      state: "committed-snapshot",
      committedSnapshot: true,
      visibleInline: false,
    })
  })

  it("records retired state after meaningful interaction displaces the warm landing", () => {
    expect(
      resolveLandingLifecycle({
        ...base,
        warmLandingVisible: false,
        transcriptCommittedCount: 2,
      }),
    ).toMatchObject({
      state: "retired",
      reason: "meaningful-interaction-displaced-warm-landing",
    })
  })

  it("records small-height suppression as a first-class state", () => {
    expect(resolveLandingLifecycle({ ...base, bodyBudgetRows: 0 })).toMatchObject({
      state: "suppressed-small-height",
      reason: "no-body-budget",
    })
  })
})
