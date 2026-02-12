export interface ReplProviderCapabilities {
  reasoningEvents: boolean
  thoughtSummaryEvents: boolean
  contextUsage: boolean
  activitySurface: boolean
  rawThinkingPeek: boolean
  inlineThinkingBlock: boolean
}

export interface ReplCapabilityOverrideSchema {
  defaults?: Record<string, unknown>
  presets?: Record<string, unknown>
  providers?: Record<string, unknown>
  models?: Record<string, unknown>
}

export interface CapabilityResolutionInput {
  readonly modelId?: string | null
  readonly preset?: string | null
  readonly overrideSchema?: unknown
  readonly runtimeOverrides?: Partial<ReplProviderCapabilities> | null
}

export interface CapabilityResolutionResult {
  readonly provider: string
  readonly model: string | null
  readonly capabilities: ReplProviderCapabilities
  readonly warnings: string[]
}

const KNOWN_CAPABILITY_KEYS = new Set<keyof ReplProviderCapabilities>([
  "reasoningEvents",
  "thoughtSummaryEvents",
  "contextUsage",
  "activitySurface",
  "rawThinkingPeek",
  "inlineThinkingBlock",
])

const BASE_CAPABILITIES: ReplProviderCapabilities = {
  reasoningEvents: true,
  thoughtSummaryEvents: true,
  contextUsage: true,
  activitySurface: true,
  rawThinkingPeek: false,
  inlineThinkingBlock: false,
}

const PRESET_CAPABILITIES: Record<string, Partial<ReplProviderCapabilities>> = {
  claude_like: {
    reasoningEvents: false,
    thoughtSummaryEvents: true,
    contextUsage: true,
    activitySurface: true,
    rawThinkingPeek: false,
    inlineThinkingBlock: false,
  },
  codex_like: {
    reasoningEvents: true,
    thoughtSummaryEvents: true,
    contextUsage: true,
    activitySurface: true,
    rawThinkingPeek: false,
    inlineThinkingBlock: true,
  },
}

const PROVIDER_CAPABILITIES: Record<string, Partial<ReplProviderCapabilities>> = {
  anthropic: {
    reasoningEvents: false,
    thoughtSummaryEvents: true,
    inlineThinkingBlock: false,
  },
  openai: {
    reasoningEvents: true,
    thoughtSummaryEvents: true,
    inlineThinkingBlock: true,
  },
  openrouter: {
    reasoningEvents: true,
    thoughtSummaryEvents: true,
    inlineThinkingBlock: true,
  },
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value)

const sanitizePatch = (
  patch: unknown,
  path: string,
  warnings: string[],
): Partial<ReplProviderCapabilities> => {
  if (!isRecord(patch)) {
    if (patch != null) warnings.push(`${path} must be an object; ignoring invalid override.`)
    return {}
  }
  const next: Partial<ReplProviderCapabilities> = {}
  for (const [key, value] of Object.entries(patch)) {
    if (!KNOWN_CAPABILITY_KEYS.has(key as keyof ReplProviderCapabilities)) {
      warnings.push(`${path}.${key} is unknown; ignoring key.`)
      continue
    }
    if (typeof value !== "boolean") {
      warnings.push(`${path}.${key} must be boolean; ignoring invalid value.`)
      continue
    }
    next[key as keyof ReplProviderCapabilities] = value
  }
  return next
}

const parseSchema = (
  input: unknown,
): { schema: ReplCapabilityOverrideSchema; warnings: string[] } => {
  const warnings: string[] = []
  if (!isRecord(input)) {
    if (input != null) warnings.push("overrideSchema must be an object; ignoring invalid schema.")
    return { schema: {}, warnings }
  }
  const schema: ReplCapabilityOverrideSchema = {}
  if (isRecord(input.defaults)) schema.defaults = input.defaults
  else if (input.defaults != null) warnings.push("overrideSchema.defaults must be an object.")
  if (isRecord(input.presets)) schema.presets = input.presets
  else if (input.presets != null) warnings.push("overrideSchema.presets must be an object.")
  if (isRecord(input.providers)) schema.providers = input.providers
  else if (input.providers != null) warnings.push("overrideSchema.providers must be an object.")
  if (isRecord(input.models)) schema.models = input.models
  else if (input.models != null) warnings.push("overrideSchema.models must be an object.")
  return { schema, warnings }
}

const parseModelIdentity = (modelId?: string | null): { provider: string; model: string | null } => {
  const trimmed = (modelId ?? "").trim()
  if (!trimmed) return { provider: "unknown", model: null }
  const slash = trimmed.indexOf("/")
  if (slash <= 0) return { provider: "unknown", model: trimmed }
  return {
    provider: trimmed.slice(0, slash).trim().toLowerCase() || "unknown",
    model: trimmed,
  }
}

export const resolveProviderCapabilities = (input: CapabilityResolutionInput): CapabilityResolutionResult => {
  const identity = parseModelIdentity(input.modelId)
  const presetName = (input.preset ?? "").trim().toLowerCase()
  const { schema, warnings } = parseSchema(input.overrideSchema)

  const defaultsPatch = sanitizePatch(schema.defaults, "defaults", warnings)
  const presetBuiltIn = presetName ? PRESET_CAPABILITIES[presetName] ?? {} : {}
  const presetCustom = presetName && schema.presets ? sanitizePatch(schema.presets[presetName], `presets.${presetName}`, warnings) : {}
  if (
    presetName &&
    PRESET_CAPABILITIES[presetName] == null &&
    !(schema.presets && isRecord(schema.presets[presetName]))
  ) {
    warnings.push(`preset "${presetName}" is unknown; using default/provider/model/runtime layers.`)
  }
  const providerBuiltIn = PROVIDER_CAPABILITIES[identity.provider] ?? {}
  const providerCustom = schema.providers
    ? sanitizePatch(schema.providers[identity.provider], `providers.${identity.provider}`, warnings)
    : {}
  const modelCustom =
    identity.model && schema.models
      ? sanitizePatch(schema.models[identity.model], `models.${identity.model}`, warnings)
      : {}
  const runtimePatch = sanitizePatch(input.runtimeOverrides ?? {}, "runtimeOverrides", warnings)

  const capabilities: ReplProviderCapabilities = {
    ...BASE_CAPABILITIES,
    ...defaultsPatch,
    ...presetBuiltIn,
    ...presetCustom,
    ...providerBuiltIn,
    ...providerCustom,
    ...modelCustom,
    ...runtimePatch,
  }

  return {
    provider: identity.provider,
    model: identity.model,
    capabilities,
    warnings,
  }
}
