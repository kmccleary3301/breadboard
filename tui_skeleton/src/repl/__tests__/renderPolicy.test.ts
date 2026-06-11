import { describe, expect, it } from "vitest"
import { resolveLiveSlotRenderPolicy } from "../renderPolicy.js"

describe("render policy registry", () => {
  it("classifies provider retry live slots as recovery diagnostics", () => {
    expect(
      resolveLiveSlotRenderPolicy({
        id: "provider-retry",
        text: "[warning] Retrying provider route gpt-5.4-mini",
        status: "warning",
      }),
    ).toMatchObject({
      componentKind: "live-slot",
      ownershipClass: "live-region",
      stabilityState: "recovery",
      contentSafetyClass: "diagnostic-summary",
      heightPolicy: "viewport-reserved",
      truncationPolicy: "collapse-detail",
      detailPolicy: "inspector-or-export",
      priority: "high",
    })
  })

  it("classifies pending tool live slots as bounded tool previews", () => {
    expect(
      resolveLiveSlotRenderPolicy({
        id: "tool-call-123",
        text: "bash: pnpm test",
        status: "pending",
      }),
    ).toMatchObject({
      componentKind: "live-slot",
      ownershipClass: "live-region",
      stabilityState: "pending",
      contentSafetyClass: "tool-output-preview",
      widthPolicy: "truncate",
      heightPolicy: "viewport-reserved",
      detailPolicy: "inspector-or-export",
    })

    expect(
      resolveLiveSlotRenderPolicy({
        id: "slot-123",
        text: "shell_command: shell_command",
        status: "pending",
      }),
    ).toMatchObject({
      componentKind: "live-slot",
      ownershipClass: "live-region",
      stabilityState: "pending",
      contentSafetyClass: "tool-output-preview",
      detailPolicy: "inspector-or-export",
    })
  })

  it("classifies transient success live slots as ephemeral status", () => {
    expect(
      resolveLiveSlotRenderPolicy({
        id: "reward",
        text: "Reward update: accepted",
        status: "success",
      }),
    ).toMatchObject({
      componentKind: "live-slot",
      ownershipClass: "live-region",
      stabilityState: "ephemeral",
      contentSafetyClass: "safe-text",
      widthPolicy: "truncate",
      heightPolicy: "viewport-reserved",
      detailPolicy: "inline-only",
      priority: "low",
    })
  })
})
