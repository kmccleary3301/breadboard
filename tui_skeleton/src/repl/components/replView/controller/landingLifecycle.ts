export interface LandingMeaningfulInteractionInput {
  readonly pendingResponse: boolean
  readonly conversationCount: number
  readonly transcriptCommittedCount: number
  readonly transcriptTailCount: number
}

export type LandingLifecycleState =
  | "fresh-visible"
  | "compact-visible"
  | "committed-snapshot"
  | "retired"
  | "suppressed-small-height"

export interface LandingLifecycleInput {
  readonly scrollbackMode: boolean
  readonly hasLandingNode: boolean
  readonly landingVariant: string | null
  readonly landingRows: number
  readonly bodyBudgetRows: number
  readonly warmLandingVisible: boolean
  readonly landingRetired: boolean
  readonly landingShouldRetireNow: boolean
  readonly appendLandingToFeed: boolean
  readonly landingAlways: boolean
  readonly transcriptCommittedCount: number
  readonly transcriptTailCount: number
  readonly pendingResponse: boolean
}

export interface LandingLifecycleSnapshot {
  readonly state: LandingLifecycleState
  readonly reason: string
  readonly visibleInline: boolean
  readonly committedSnapshot: boolean
}

// Warm landing should retire only after transcript-visible interaction begins.
// Background startup metadata churn must not count as meaningful interaction.
export const hasWarmLandingMeaningfulInteraction = (
  input: LandingMeaningfulInteractionInput,
): boolean =>
  input.pendingResponse ||
  input.conversationCount > 0 ||
  input.transcriptCommittedCount > 0 ||
  input.transcriptTailCount > 0

const isCompactVariant = (variant: string | null): boolean =>
  variant === "compact" || variant === "micro" || variant === "minimal" || variant === "tiny"

export const resolveLandingLifecycle = (input: LandingLifecycleInput): LandingLifecycleSnapshot => {
  const {
    scrollbackMode,
    hasLandingNode,
    landingVariant,
    landingRows,
    bodyBudgetRows,
    warmLandingVisible,
    landingRetired,
    landingShouldRetireNow,
    appendLandingToFeed,
    landingAlways,
    transcriptCommittedCount,
    transcriptTailCount,
    pendingResponse,
  } = input

  if (!scrollbackMode) {
    return {
      state: "retired",
      reason: "non-scrollback-mode",
      visibleInline: false,
      committedSnapshot: false,
    }
  }

  if (!hasLandingNode || landingRows <= 0 || bodyBudgetRows <= 0) {
    return {
      state: "suppressed-small-height",
      reason: !hasLandingNode ? "landing-node-missing" : landingRows <= 0 ? "landing-has-no-rows" : "no-body-budget",
      visibleInline: false,
      committedSnapshot: false,
    }
  }

  if (appendLandingToFeed && (landingRetired || landingShouldRetireNow)) {
    return {
      state: "committed-snapshot",
      reason: landingShouldRetireNow ? "retiring-to-static-feed" : "static-feed-preserves-retired-landing",
      visibleInline: false,
      committedSnapshot: true,
    }
  }

  if (landingRetired || landingAlways) {
    return {
      state: "retired",
      reason: landingAlways ? "landing-always-disabled-for-inline-warm-path" : "landing-retired-after-meaningful-interaction",
      visibleInline: false,
      committedSnapshot: false,
    }
  }

  if (warmLandingVisible) {
    const compact = isCompactVariant(landingVariant)
    return {
      state: compact ? "compact-visible" : "fresh-visible",
      reason: compact ? "compact-landing-fits-active-region" : "rich-landing-fits-active-region",
      visibleInline: true,
      committedSnapshot: false,
    }
  }

  if (transcriptCommittedCount > 0 || transcriptTailCount > 0 || pendingResponse) {
    return {
      state: "retired",
      reason: "meaningful-interaction-displaced-warm-landing",
      visibleInline: false,
      committedSnapshot: false,
    }
  }

  return {
    state: "suppressed-small-height",
    reason: "landing-does-not-fit-startup-budget",
    visibleInline: false,
    committedSnapshot: false,
  }
}
