import { describe, expect, it } from "vitest"
import {
  buildLandingContext,
  buildScrollbackLandingLines,
  buildScrollbackSessionHeaderLines,
  resolveLandingVariantForViewport,
} from "../scrollbackLanding.js"

describe("resolveLandingVariantForViewport", () => {
  const common = {
    contentWidth: 148,
    modelLabel: "gpt-5.4-mini",
    chromeLabel: "Claude Code",
    configLabel: "Claude Code",
    cwd: "/repo",
    borderStyle: "round" as const,
    showAsciiArt: true,
  }

  it("keeps the richer width-preferred landing when it fits", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        preferredVariant: "auto",
        maxRows: 20,
      }),
    ).toBe("hero")
  })

  it("falls back to a shorter landing when the board variant does not fit", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        preferredVariant: "auto",
        maxRows: 12,
      }),
    ).toBe("split")
  })

  it("respects explicit variants", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        preferredVariant: "compact",
        maxRows: 20,
      }),
    ).toBe("compact")
  })

  it("uses the micro variant for very narrow widths", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        contentWidth: 32,
        preferredVariant: "auto",
        maxRows: 4,
      }),
    ).toBe("micro")
  })

  it("suppresses the landing when even micro would clip the body budget", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        contentWidth: 32,
        preferredVariant: "auto",
        maxRows: 0,
      }),
    ).toBe("suppressed")
  })

  it("treats explicit rich variants as preferences rather than clipping permission", () => {
    expect(
      resolveLandingVariantForViewport({
        ...common,
        preferredVariant: "hero",
        maxRows: 4,
      }),
    ).toBe("split")

    expect(
      resolveLandingVariantForViewport({
        ...common,
        preferredVariant: "hero",
        maxRows: 0,
      }),
    ).toBe("suppressed")
  })
})


describe("buildScrollbackSessionHeaderLines", () => {
  it("keeps compact identity while surfacing turn and session continuity", () => {
    const lines = buildScrollbackSessionHeaderLines(
      buildLandingContext({
        contentWidth: 120,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Claude Code",
        configLabel: "Claude Code",
        cwd: "/repo",
        sessionLabel: "s abcdef12",
        statusLabel: "resumed · turn 4",
        variant: "compact",
        borderStyle: "round",
        showAsciiArt: false,
      }),
    )

    expect(lines[0]).toContain("BreadBoard")
    expect(lines[1]).toContain("gpt-5.4-mini")
    expect(lines[1]).toContain("/repo")
    expect(lines[1]).toContain("turn 4")
    expect(lines[1]).toContain("s abcdef12")
  })
})

describe("buildScrollbackLandingLines", () => {
  it("renders the hero alias with the rich board pane", () => {
    const lines = buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth: 120,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Codex",
        configLabel: "Codex",
        cwd: "/repo",
        variant: "hero",
        borderStyle: "round",
        showAsciiArt: true,
      }),
    )

    const text = lines.join("\n")
    expect(text).toContain("Tips for getting started")
    expect(text).toContain("Recent activity")
    expect(text).toContain("BreadBoard v")
  })

  it("keeps the medium split landing branded without an outer border", () => {
    const lines = buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth: 88,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Claude Code",
        configLabel: "Claude Code",
        cwd: "/repo",
        variant: "split",
        borderStyle: "round",
        showAsciiArt: true,
      }),
    )

    expect(lines).toHaveLength(4)
    expect(lines.join("\n")).toContain("BreadBoard v")
    expect(lines.join("\n")).toContain("Using Config")
    expect(lines.join("\n")).toContain("gpt-5.4-mini")
    expect(lines.some((line) => line.includes("╭") || line.includes("╰"))).toBe(false)
  })

  it("keeps the narrow compact landing branded instead of falling back to a session header", () => {
    const lines = buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth: 72,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Claude Code",
        configLabel: "Claude Code",
        cwd: "/repo",
        variant: "compact",
        borderStyle: "round",
        showAsciiArt: true,
      }),
    )

    const text = lines.join("\n")
    expect(text).toContain("BreadBoard v")
    expect(text).toContain("Hello again")
    expect(text).toContain("Config: Claude Code")
    expect(text).not.toContain("BreadBoard · Claude Code")
  })

  it("renders micro as a one-line identity fallback", () => {
    const lines = buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth: 36,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Codex",
        configLabel: "Codex",
        cwd: "/repo",
        variant: "micro",
        borderStyle: "round",
        showAsciiArt: true,
      }),
    )

    expect(lines).toHaveLength(1)
    expect(lines[0]).toContain("BreadBoard v")
  })

  it("renders suppressed as no landing lines", () => {
    const lines = buildScrollbackLandingLines(
      buildLandingContext({
        contentWidth: 36,
        modelLabel: "gpt-5.4-mini",
        chromeLabel: "Codex",
        configLabel: "Codex",
        cwd: "/repo",
        variant: "suppressed",
        borderStyle: "round",
        showAsciiArt: true,
      }),
    )

    expect(lines).toEqual([])
  })
})
