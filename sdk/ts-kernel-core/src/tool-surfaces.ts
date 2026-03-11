import { createHash } from "node:crypto"

import {
  assertValid,
  type EffectiveToolSurfaceV1,
  type EnvironmentSelectorV1,
  type ToolBindingV1,
  type ToolSupportClaimV1,
} from "@breadboard/kernel-contracts"

export interface ToolPackDefinition {
  readonly packId: string
  readonly toolIds: readonly string[]
  readonly bindingIds?: readonly string[]
  readonly defaultBindingIds?: readonly string[]
  readonly environmentSelector?: EnvironmentSelectorV1
  readonly tags?: readonly string[]
  readonly metadata?: Readonly<Record<string, unknown>>
}

export interface EffectiveToolSurfaceResolutionInput {
  readonly surfaceId: string
  readonly bindings: readonly ToolBindingV1[]
  readonly claims: readonly ToolSupportClaimV1[]
  readonly projectionProfileId?: string | null
  readonly profileId?: string | null
  readonly providerFamily?: string | null
  readonly driverClass?: string | null
  readonly imageId?: string | null
  readonly serviceIds?: readonly string[]
  readonly features?: readonly string[]
  readonly toolPacks?: readonly ToolPackDefinition[]
  readonly activePackIds?: readonly string[]
}

export interface ResolvedToolBinding {
  readonly toolId: string
  readonly binding: ToolBindingV1
  readonly claim: ToolSupportClaimV1 | null
  readonly selectedViaFallback: boolean
  readonly resolutionPath: readonly string[]
}

export interface EffectiveToolSurfaceEntry {
  readonly toolId: string
  readonly bindingId: string | null
  readonly bindingKind: ToolBindingV1["binding_kind"] | null
  readonly level: ToolSupportClaimV1["level"] | "unbound"
  readonly summary: string
  readonly exposedToModel: boolean
  readonly selectedViaFallback: boolean
  readonly hiddenReason: string | null
  readonly resolutionPath: readonly string[]
}

export interface EffectiveToolSurfaceAnalysis {
  readonly surface: EffectiveToolSurfaceV1
  readonly visibleEntries: readonly EffectiveToolSurfaceEntry[]
  readonly hiddenEntries: readonly EffectiveToolSurfaceEntry[]
  readonly unsupportedEntries: readonly EffectiveToolSurfaceEntry[]
}

function matchesSelector(
  selector: EnvironmentSelectorV1 | null | undefined,
  input: EffectiveToolSurfaceResolutionInput,
): boolean {
  if (!selector) return true
  if (selector.profile_ids?.length && !selector.profile_ids.includes(input.profileId ?? "")) {
    return false
  }
  if (selector.provider_families?.length && !selector.provider_families.includes(input.providerFamily ?? "")) {
    return false
  }
  if (selector.driver_classes?.length && !selector.driver_classes.includes(input.driverClass ?? "")) {
    return false
  }
  if (selector.image_ids?.length && !selector.image_ids.includes(input.imageId ?? "")) {
    return false
  }
  if (selector.service_ids?.length) {
    const available = new Set(input.serviceIds ?? [])
    if (!selector.service_ids.every((serviceId) => available.has(serviceId))) {
      return false
    }
  }
  if (selector.features?.length) {
    const available = new Set(input.features ?? [])
    if (!selector.features.every((feature) => available.has(feature))) {
      return false
    }
  }
  return true
}

function buildSurfaceHash(toolIds: readonly string[], bindingIds: readonly string[]): string {
  return `sha256:${createHash("sha256")
    .update(JSON.stringify({ tool_ids: [...toolIds], binding_ids: [...bindingIds] }))
    .digest("hex")}`
}

function buildActivePackSets(input: EffectiveToolSurfaceResolutionInput): {
  toolIds: Set<string> | null
  bindingIds: Set<string> | null
} {
  if (!input.toolPacks?.length) {
    return { toolIds: null, bindingIds: null }
  }
  const activePackIds = new Set(input.activePackIds ?? input.toolPacks.map((pack) => pack.packId))
  const toolIds = new Set<string>()
  const bindingIds = new Set<string>()
  for (const pack of input.toolPacks) {
    if (!activePackIds.has(pack.packId)) continue
    if (!matchesSelector(pack.environmentSelector ?? null, input)) continue
    for (const toolId of pack.toolIds) toolIds.add(toolId)
    for (const bindingId of [...(pack.bindingIds ?? []), ...(pack.defaultBindingIds ?? [])]) bindingIds.add(bindingId)
  }
  return { toolIds, bindingIds }
}

function claimPriority(claim: ToolSupportClaimV1 | null): number {
  switch (claim?.level) {
    case "supported":
      return 4
    case "degraded":
      return 3
    case "install_required":
      return 2
    case "unsupported":
      return 1
    default:
      return 0
  }
}

function compareResolvedCandidates(
  next: {
    readonly claim: ToolSupportClaimV1 | null
    readonly selectedViaFallback: boolean
    readonly resolutionPath: readonly string[]
    readonly bindingId: string
  },
  current: {
    readonly claim: ToolSupportClaimV1 | null
    readonly selectedViaFallback: boolean
    readonly resolutionPath: readonly string[]
    readonly bindingId: string
  },
): number {
  const priorityDelta = claimPriority(next.claim) - claimPriority(current.claim)
  if (priorityDelta !== 0) return priorityDelta

  // Prefer a direct binding over a fallback-selected binding when both are otherwise equal.
  if (next.selectedViaFallback !== current.selectedViaFallback) {
    return next.selectedViaFallback ? -1 : 1
  }

  // Prefer the shorter resolution path when priorities and fallback status are equal.
  if (next.resolutionPath.length !== current.resolutionPath.length) {
    return current.resolutionPath.length - next.resolutionPath.length
  }

  // Preserve deterministic output for ties by falling back to binding id ordering.
  if (next.bindingId === current.bindingId) {
    return 0
  }
  return next.bindingId < current.bindingId ? 1 : -1
}

function isModelVisibleClaim(claim: ToolSupportClaimV1 | null): boolean {
  if (!claim) return false
  if (claim.level === "hidden") return false
  if (claim.exposed_to_model === false) return false
  return true
}

function chooseBindingForTool(options: {
  readonly toolId: string
  readonly candidates: readonly ToolBindingV1[]
  readonly input: EffectiveToolSurfaceResolutionInput
  readonly claimByBindingId: ReadonlyMap<string, ToolSupportClaimV1>
  readonly claimsByToolId: ReadonlyMap<string, readonly ToolSupportClaimV1[]>
}): ResolvedToolBinding | null {
  const fallbackBindingIds = new Set(
    options.candidates.flatMap((binding) => binding.fallback_binding_ids ?? []),
  )
  const rootCandidates = options.candidates.filter((binding) => !fallbackBindingIds.has(binding.binding_id))
  const queue = (rootCandidates.length > 0 ? rootCandidates : options.candidates).map((binding) => ({
    binding,
    path: [binding.binding_id],
    selectedViaFallback: false,
  }))
  const seen = new Set<string>()
  let best: ResolvedToolBinding | null = null

  while (queue.length > 0) {
    const candidate = queue.shift()!
    const binding = candidate.binding
    if (seen.has(binding.binding_id)) continue
    seen.add(binding.binding_id)

    const claim =
      options.claimByBindingId.get(binding.binding_id) ??
      options.claimsByToolId.get(options.toolId)?.find((candidate) => candidate.binding_id === binding.binding_id) ??
      options.claimsByToolId.get(options.toolId)?.[0] ??
      null

    const eligible = matchesSelector(binding.environment_selector ?? null, options.input)
    if (eligible && isModelVisibleClaim(claim)) {
      const resolvedCandidate: ResolvedToolBinding = {
        toolId: options.toolId,
        binding,
        claim,
        selectedViaFallback: candidate.selectedViaFallback,
        resolutionPath: candidate.path,
      }
      if (
        !best ||
        compareResolvedCandidates(
          {
            claim,
            selectedViaFallback: candidate.selectedViaFallback,
            resolutionPath: candidate.path,
            bindingId: binding.binding_id,
          },
          {
            claim: best.claim,
            selectedViaFallback: best.selectedViaFallback,
            resolutionPath: best.resolutionPath,
            bindingId: best.binding.binding_id,
          },
        ) > 0
      ) {
        best = resolvedCandidate
      }
    }

    for (const fallbackId of binding.fallback_binding_ids ?? []) {
      const fallback = options.candidates.find((candidate) => candidate.binding_id === fallbackId)
      if (fallback && !seen.has(fallback.binding_id)) {
        queue.push({
          binding: fallback,
          path: [...candidate.path, fallback.binding_id],
          selectedViaFallback: true,
        })
      }
    }
  }

  return best
}

export function resolveToolBindings(input: EffectiveToolSurfaceResolutionInput): ResolvedToolBinding[] {
  const visibleClaims = input.claims.filter((claim) => claim.level !== "hidden")
  const claimsByToolId = new Map<string, ToolSupportClaimV1[]>()
  const claimByBindingId = new Map<string, ToolSupportClaimV1>()
  for (const claim of visibleClaims) {
    const existing = claimsByToolId.get(claim.tool_id) ?? []
    claimsByToolId.set(claim.tool_id, [...existing, claim])
    if (claim.binding_id) {
      claimByBindingId.set(claim.binding_id, claim)
    }
  }

  const activePackSets = buildActivePackSets(input)
  const scopedBindings = input.bindings.filter((binding) => {
    if (activePackSets.toolIds && !activePackSets.toolIds.has(binding.tool_id)) {
      return false
    }
    if (activePackSets.bindingIds && activePackSets.bindingIds.size > 0 && !activePackSets.bindingIds.has(binding.binding_id)) {
      return false
    }
    return true
  })

  const bindingsByToolId = new Map<string, ToolBindingV1[]>()
  for (const binding of scopedBindings) {
    const existing = bindingsByToolId.get(binding.tool_id) ?? []
    bindingsByToolId.set(binding.tool_id, [...existing, binding])
  }

  return [...bindingsByToolId.entries()]
    .map(([toolId, candidates]) =>
      chooseBindingForTool({
        toolId,
        candidates,
        input,
        claimByBindingId,
        claimsByToolId,
      }),
    )
    .filter((resolved): resolved is ResolvedToolBinding => resolved != null)
    .sort((left, right) => {
      if (left.toolId !== right.toolId) {
        return left.toolId.localeCompare(right.toolId)
      }
      return left.binding.binding_id.localeCompare(right.binding.binding_id)
    })
}

function buildEffectiveToolSurfaceFromEntries(options: {
  readonly surfaceId: string
  readonly projectionProfileId?: string | null
  readonly visibleEntries: readonly EffectiveToolSurfaceEntry[]
  readonly hiddenEntries: readonly EffectiveToolSurfaceEntry[]
}): EffectiveToolSurfaceV1 {
  const toolIds = options.visibleEntries.map((resolved) => resolved.toolId)
  const bindingIds = options.visibleEntries.flatMap((resolved) => (resolved.bindingId ? [resolved.bindingId] : []))
  const hiddenToolIds = options.hiddenEntries.map((claim) => claim.toolId)
  return assertValid<EffectiveToolSurfaceV1>("effectiveToolSurface", {
    schema_version: "bb.effective_tool_surface.v1",
    surface_id: options.surfaceId,
    tool_ids: toolIds,
    binding_ids: bindingIds,
    hidden_tool_ids: hiddenToolIds,
    projection_profile_id: options.projectionProfileId ?? null,
    surface_hash: buildSurfaceHash(toolIds, bindingIds),
  })
}

function buildToolEntry(options: {
  readonly toolId: string
  readonly binding: ToolBindingV1 | null
  readonly claim: ToolSupportClaimV1 | null
  readonly selectedViaFallback?: boolean
  readonly resolutionPath?: readonly string[]
}): EffectiveToolSurfaceEntry {
  const exposedToModel = !!options.claim && isModelVisibleClaim(options.claim)
  return {
    toolId: options.toolId,
    bindingId: options.binding?.binding_id ?? options.claim?.binding_id ?? null,
    bindingKind: options.binding?.binding_kind ?? null,
    level: options.claim?.level ?? "unbound",
    summary: options.claim?.summary ?? "No compatible binding was selected.",
    exposedToModel,
    selectedViaFallback: options.selectedViaFallback ?? false,
    hiddenReason: options.claim?.hidden_reason ?? null,
    resolutionPath: options.resolutionPath ?? (options.binding ? [options.binding.binding_id] : []),
  }
}

export function analyzeEffectiveToolSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceAnalysis {
  const resolvedBindings = resolveToolBindings(input)
  const resolvedByToolId = new Map(resolvedBindings.map((resolved) => [resolved.toolId, resolved] as const))
  const visibleEntries = resolvedBindings.map((resolved) =>
    buildToolEntry({
      toolId: resolved.toolId,
      binding: resolved.binding,
      claim: resolved.claim,
      selectedViaFallback: resolved.selectedViaFallback,
      resolutionPath: resolved.resolutionPath,
    }),
  )
  const hiddenEntries = input.claims
    .filter((claim) => !resolvedByToolId.has(claim.tool_id) && (claim.level === "hidden" || claim.exposed_to_model === false))
    .map((claim) =>
      buildToolEntry({
        toolId: claim.tool_id,
        binding: input.bindings.find((binding) => binding.binding_id === claim.binding_id) ?? null,
        claim,
      }),
    )
  const unsupportedEntries = input.claims
    .filter(
      (claim) =>
        !resolvedByToolId.has(claim.tool_id) &&
        claim.level !== "hidden" &&
        claim.exposed_to_model !== false,
    )
    .map((claim) =>
      buildToolEntry({
        toolId: claim.tool_id,
        binding: input.bindings.find((binding) => binding.binding_id === claim.binding_id) ?? null,
        claim,
      }),
    )
  const sortEntries = (entries: EffectiveToolSurfaceEntry[]): EffectiveToolSurfaceEntry[] =>
    [...entries].sort((left, right) => {
      if (left.toolId !== right.toolId) {
        return left.toolId.localeCompare(right.toolId)
      }
      return (left.bindingId ?? "").localeCompare(right.bindingId ?? "")
    })
  return {
    surface: buildEffectiveToolSurfaceFromEntries({
      surfaceId: input.surfaceId,
      projectionProfileId: input.projectionProfileId ?? null,
      visibleEntries: sortEntries(visibleEntries),
      hiddenEntries: sortEntries(hiddenEntries),
    }),
    visibleEntries: sortEntries(visibleEntries),
    hiddenEntries: sortEntries(hiddenEntries),
    unsupportedEntries: sortEntries(unsupportedEntries),
  }
}

export function resolveEffectiveToolSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceV1 {
  return analyzeEffectiveToolSurface(input).surface
}
