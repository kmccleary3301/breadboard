import { describe, expect, it } from "vitest"
import { Option } from "effect"
import {
  ANALYSIS_REQUEST_CONFIG_PATH,
  DEFAULT_REQUEST_CONFIG_PATH,
  resolveInvocationContext,
} from "../src/commands/invocationContext.js"

describe("resolveInvocationContext", () => {
  it("applies fallback model and interactive permission defaults", () => {
    const context = resolveInvocationContext({
      config: DEFAULT_REQUEST_CONFIG_PATH,
      workspace: null,
      overrides: null,
      metadata: null,
      model: null,
      remoteStream: Option.some(true),
      permissionMode: "ask",
      analysis: false,
      fallbackModel: "openai/test-model",
    })

    expect(context.resolvedModel).toBe("openai/test-model")
    expect(context.remotePreference).toBe(true)
    expect(context.requestMetadata.model).toBe("openai/test-model")
    expect(context.requestMetadata.permission_mode).toBe("ask")
    expect(context.requestOverrides["providers.default_model"]).toBe("openai/test-model")
    expect(context.requestOverrides["permissions.options.mode"]).toBe("prompt")
    expect(context.requestOverrides["permissions.edit.default"]).toBe("ask")
  })

  it("switches to analysis config only when using the default config marker", () => {
    const context = resolveInvocationContext({
      config: DEFAULT_REQUEST_CONFIG_PATH,
      workspace: null,
      overrides: null,
      metadata: null,
      model: null,
      remoteStream: Option.none(),
      permissionMode: null,
      analysis: true,
      fallbackModel: null,
    })

    expect(context.resolvedConfigPath.endsWith(ANALYSIS_REQUEST_CONFIG_PATH)).toBe(true)
    expect(context.requestMetadata.mode).toBe("analysis")
    expect(context.requestMetadata.permission_mode).toBe("analysis")
  })

  it("preserves override model and leaves interactive defaults off for non-interactive permission modes", () => {
    const context = resolveInvocationContext({
      config: "agent_configs/custom.yaml",
      workspace: null,
      overrides: '{"providers.default_model":"override/model"}',
      metadata: '{"x":1}',
      model: null,
      remoteStream: Option.none(),
      permissionMode: "auto",
      analysis: false,
      fallbackModel: null,
    })

    expect(context.resolvedModel).toBe("override/model")
    expect(context.requestMetadata.model).toBe("override/model")
    expect(context.requestMetadata.x).toBe(1)
    expect(context.requestMetadata.permission_mode).toBe("auto")
    expect(context.requestOverrides["providers.default_model"]).toBe("override/model")
    expect(context.requestOverrides["permissions.options.mode"]).toBeUndefined()
  })
})
