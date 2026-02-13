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
  readonly providerWhitelist?: ReadonlyArray<string> | null
  readonly modelWhitelist?: ReadonlyArray<string> | null
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

const DEFAULT_PROVIDER_WHITELIST = new Set<string>(["anthropic", "openai", "openrouter", "unknown"])

const isRecord = (value: unknown): value is Record<string, unknown> =>
  value != null && typeof value === "object" && !Array.isArray(value)

const normalizeProviderKey = (value: unknown): string =>
  String(value ?? "")
    .trim()
    .toLowerCase()

const parseProviderWhitelist = (input?: ReadonlyArray<string> | null): ReadonlySet<string> => {
  if (!Array.isArray(input) || input.length === 0) return DEFAULT_PROVIDER_WHITELIST
  const values = input.map((value) => normalizeProviderKey(value)).filter((value) => value.length > 0)
  return values.length > 0 ? new Set(values) : DEFAULT_PROVIDER_WHITELIST
}

const normalizeModelKey = (
  value: unknown,
): {
  readonly normalized: string | null
  readonly provider: string | null
  readonly valid: boolean
  readonly reason?: string
} => {
  const raw = String(value ?? "").trim()
  if (!raw) return { normalized: null, provider: null, valid: false, reason: "model key is empty" }
  const slash = raw.indexOf("/")
  if (slash <= 0) {
    return { normalized: raw, provider: "unknown", valid: true }
  }
  const provider = normalizeProviderKey(raw.slice(0, slash))
  const modelPart = raw.slice(slash + 1).trim()
  if (!provider) return { normalized: null, provider: null, valid: false, reason: "provider prefix is empty" }
  if (!modelPart) return { normalized: null, provider, valid: false, reason: "model suffix is empty" }
  return { normalized: `${provider}/${modelPart}`, provider, valid: true }
}

const parseModelWhitelist = (
  input: ReadonlyArray<string> | null | undefined,
  providerWhitelist: ReadonlySet<string>,
  warnings: string[],
): ReadonlySet<string> | null => {
  if (!Array.isArray(input) || input.length === 0) return null
  const accepted: string[] = []
  for (const entry of input) {
    const normalized = normalizeModelKey(entry)
    if (!normalized.valid || !normalized.normalized) {
      warnings.push(`modelWhitelist entry "${String(entry)}" is invalid; ignoring entry.`)
      continue
    }
    const provider = normalized.provider ?? "unknown"
    if (!providerWhitelist.has(provider)) {
      warnings.push(`modelWhitelist entry "${String(entry)}" rejected: provider "${provider}" not in provider whitelist.`)
      continue
    }
    accepted.push(normalized.normalized)
  }
  return accepted.length > 0 ? new Set(accepted) : null
}

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
  providerWhitelist: ReadonlySet<string>,
  modelWhitelist: ReadonlySet<string> | null,
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
  if (isRecord(input.providers)) {
    const providers: Record<string, unknown> = {}
    for (const [rawProvider, patch] of Object.entries(input.providers)) {
      const provider = normalizeProviderKey(rawProvider)
      if (!provider) {
        warnings.push(`providers.${rawProvider} rejected: provider key is empty.`)
        continue
      }
      if (!providerWhitelist.has(provider)) {
        warnings.push(`providers.${rawProvider} rejected: provider "${provider}" is not in whitelist.`)
        continue
      }
      providers[provider] = patch
    }
    schema.providers = providers
  } else if (input.providers != null) warnings.push("overrideSchema.providers must be an object.")
  if (isRecord(input.models)) {
    const models: Record<string, unknown> = {}
    for (const [rawModel, patch] of Object.entries(input.models)) {
      const normalized = normalizeModelKey(rawModel)
      if (!normalized.valid || !normalized.normalized) {
        warnings.push(`models.${rawModel} rejected: ${normalized.reason ?? "invalid model key"}.`)
        continue
      }
      const provider = normalized.provider ?? "unknown"
      if (!providerWhitelist.has(provider)) {
        warnings.push(`models.${rawModel} rejected: provider "${provider}" is not in whitelist.`)
        continue
      }
      if (modelWhitelist && !modelWhitelist.has(normalized.normalized)) {
        warnings.push(`models.${rawModel} rejected: model "${normalized.normalized}" is not in whitelist.`)
        continue
      }
      models[normalized.normalized] = patch
    }
    schema.models = models
  } else if (input.models != null) warnings.push("overrideSchema.models must be an object.")
  return { schema, warnings }
}

const parseModelIdentity = (modelId?: string | null): { provider: string; model: string | null } => {
  const normalized = normalizeModelKey(modelId ?? "")
  if (!normalized.valid || !normalized.normalized) return { provider: "unknown", model: null }
  const slash = normalized.normalized.indexOf("/")
  if (slash <= 0) return { provider: "unknown", model: normalized.normalized }
  return {
    provider: normalized.normalized.slice(0, slash).trim().toLowerCase() || "unknown",
    model: normalized.normalized,
  }
}

export const resolveProviderCapabilities = (input: CapabilityResolutionInput): CapabilityResolutionResult => {
  const identity = parseModelIdentity(input.modelId)
  const presetName = (input.preset ?? "").trim().toLowerCase()
  const providerWhitelist = parseProviderWhitelist(input.providerWhitelist)
  const bootstrapWarnings: string[] = []
  const modelWhitelist = parseModelWhitelist(input.modelWhitelist, providerWhitelist, bootstrapWarnings)
  const { schema, warnings } = parseSchema(input.overrideSchema, providerWhitelist, modelWhitelist)
  if (bootstrapWarnings.length > 0) {
    warnings.unshift(...bootstrapWarnings)
  }

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
