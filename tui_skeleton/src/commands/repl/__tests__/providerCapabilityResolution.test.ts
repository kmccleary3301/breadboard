import { describe, expect, it } from "vitest"
import { resolveProviderCapabilities } from "../providerCapabilityResolution.js"

describe("provider capability resolution", () => {
  it("applies deterministic layering default -> preset -> provider -> model -> runtime", () => {
    const result = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      preset: "claude_like",
      overrideSchema: {
        defaults: { reasoningEvents: true, rawThinkingPeek: false, inlineThinkingBlock: false },
        presets: { claude_like: { contextUsage: false } },
        providers: { openai: { reasoningEvents: false, inlineThinkingBlock: true } },
        models: { "openai/gpt-5-mini": { reasoningEvents: true, inlineThinkingBlock: true } },
      },
      runtimeOverrides: { rawThinkingPeek: true },
    })

    expect(result.provider).toBe("openai")
    expect(result.model).toBe("openai/gpt-5-mini")
    expect(result.capabilities.reasoningEvents).toBe(true)
    expect(result.capabilities.contextUsage).toBe(false)
    expect(result.capabilities.rawThinkingPeek).toBe(true)
    expect(result.capabilities.inlineThinkingBlock).toBe(true)
  })

  it("degrades safely and emits warnings for malformed schema values", () => {
    const result = resolveProviderCapabilities({
      modelId: "anthropic/claude-sonnet",
      overrideSchema: {
        defaults: { unknownFlag: true, reasoningEvents: "yes" },
        providers: "invalid",
      },
    })

    expect(result.capabilities.reasoningEvents).toBe(false)
    expect(result.warnings.length).toBeGreaterThan(0)
    expect(result.warnings.join(" ")).toContain("defaults")
    expect(result.warnings.join(" ")).toContain("providers")
  })

  it("falls back to unknown provider defaults when model identity is missing provider prefix", () => {
    const result = resolveProviderCapabilities({
      modelId: "gpt-5-no-provider-prefix",
      overrideSchema: {
        models: {
          "gpt-5-no-provider-prefix": { thoughtSummaryEvents: false },
        },
      },
    })

    expect(result.provider).toBe("unknown")
    expect(result.model).toBe("gpt-5-no-provider-prefix")
    expect(result.capabilities.thoughtSummaryEvents).toBe(false)
  })

  it("keeps layering deterministic when provider defaults conflict with preset defaults", () => {
    const anthropicResult = resolveProviderCapabilities({
      modelId: "anthropic/claude-sonnet",
      preset: "codex_like",
    })
    expect(anthropicResult.capabilities.reasoningEvents).toBe(false)
    expect(anthropicResult.capabilities.thoughtSummaryEvents).toBe(true)

    const openaiResult = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      preset: "claude_like",
    })
    expect(openaiResult.capabilities.reasoningEvents).toBe(true)
    expect(openaiResult.capabilities.thoughtSummaryEvents).toBe(true)
  })

  it("warns when an unknown preset is requested without a custom override", () => {
    const result = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      preset: "not_a_real_preset",
      overrideSchema: {},
    })

    expect(result.warnings.some((warning) => warning.includes("unknown"))).toBe(true)
  })

  it("covers provider matrix for openai/anthropic/openrouter/unknown identities", () => {
    const openai = resolveProviderCapabilities({ modelId: "openai/gpt-5-mini", preset: "codex_like" })
    const anthropic = resolveProviderCapabilities({ modelId: "anthropic/claude-sonnet", preset: "codex_like" })
    const openrouter = resolveProviderCapabilities({ modelId: "openrouter/kimi", preset: "codex_like" })
    const unknown = resolveProviderCapabilities({ modelId: "plain-model-without-provider", preset: "codex_like" })

    expect(openai.provider).toBe("openai")
    expect(anthropic.provider).toBe("anthropic")
    expect(openrouter.provider).toBe("openrouter")
    expect(unknown.provider).toBe("unknown")

    expect(openai.capabilities.reasoningEvents).toBe(true)
    expect(anthropic.capabilities.reasoningEvents).toBe(false)
    expect(openrouter.capabilities.reasoningEvents).toBe(true)
    expect(unknown.capabilities.reasoningEvents).toBe(true)
    expect(openai.capabilities.inlineThinkingBlock).toBe(true)
    expect(anthropic.capabilities.inlineThinkingBlock).toBe(false)
  })

  it("emits deterministic warnings for malformed nested override keys and value types", () => {
    const result = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      preset: "unknown_custom",
      overrideSchema: {
        presets: {
          unknown_custom: {
            badNested: { x: 1 },
            reasoningEvents: "not-bool",
          },
        },
        providers: {
          openai: {
            thoughtSummaryEvents: "bad",
            extraToggle: true,
          },
        },
      },
    })

    const warningText = result.warnings.join(" | ")
    expect(warningText).toContain("unknown")
    expect(warningText).toContain("must be boolean")
    expect(warningText).toContain("unknown; ignoring key")
    expect(result.capabilities.reasoningEvents).toBe(true)
    expect(result.capabilities.thoughtSummaryEvents).toBe(true)
  })

  it("rejects provider and model overrides outside whitelist with clear diagnostics", () => {
    const result = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      overrideSchema: {
        providers: {
          typo_provider: { reasoningEvents: false },
        },
        models: {
          "badprovider/model-x": { thoughtSummaryEvents: false },
          "openai/": { thoughtSummaryEvents: false },
        },
      },
    })

    const warningText = result.warnings.join(" | ")
    expect(warningText).toContain("providers.typo_provider rejected")
    expect(warningText).toContain("models.badprovider/model-x rejected")
    expect(warningText).toContain("models.openai/ rejected")
    expect(result.capabilities.reasoningEvents).toBe(true)
    expect(result.capabilities.thoughtSummaryEvents).toBe(true)
  })

  it("accepts explicitly allowlisted provider/model override keys", () => {
    const result = resolveProviderCapabilities({
      modelId: "azure/gpt-4.1",
      providerWhitelist: ["openai", "azure", "unknown"],
      modelWhitelist: ["azure/gpt-4.1"],
      overrideSchema: {
        providers: {
          azure: { reasoningEvents: false },
        },
        models: {
          "azure/gpt-4.1": { thoughtSummaryEvents: false },
        },
      },
    })

    expect(result.warnings.join(" | ")).not.toContain("rejected")
    expect(result.provider).toBe("azure")
    expect(result.model).toBe("azure/gpt-4.1")
    expect(result.capabilities.reasoningEvents).toBe(false)
    expect(result.capabilities.thoughtSummaryEvents).toBe(false)
  })

  it("rejects model overrides missing from explicit model whitelist", () => {
    const result = resolveProviderCapabilities({
      modelId: "openai/gpt-5-mini",
      modelWhitelist: ["openai/gpt-4.1"],
      overrideSchema: {
        models: {
          "openai/gpt-5-mini": { thoughtSummaryEvents: false },
        },
      },
    })

    expect(result.warnings.some((warning) => warning.includes("not in whitelist"))).toBe(true)
    expect(result.capabilities.thoughtSummaryEvents).toBe(true)
  })
})
