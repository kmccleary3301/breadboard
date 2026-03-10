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
    for (const toolId of pack.toolIds) toolIds.add(toolId)
    for (const bindingId of pack.bindingIds ?? []) bindingIds.add(bindingId)
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

function isModelVisibleClaim(claim: ToolSupportClaimV1 | null): boolean {
  if (!claim) return false
  if (claim.level === "hidden") return false
  if (claim.exposed_to_model === false) return false
  return true
}

function chooseBindingForTool(options: {
  readonly toolId: string
  readonly candidates: readonly ToolBindingV1[]
  readonly claimByBindingId: ReadonlyMap<string, ToolSupportClaimV1>
  readonly claimsByToolId: ReadonlyMap<string, readonly ToolSupportClaimV1[]>
}): ResolvedToolBinding | null {
  const queue = [...options.candidates]
  const seen = new Set<string>()
  let best: ResolvedToolBinding | null = null

  while (queue.length > 0) {
    const binding = queue.shift()!
    if (seen.has(binding.binding_id)) continue
    seen.add(binding.binding_id)

    const claim =
      options.claimByBindingId.get(binding.binding_id) ??
      options.claimsByToolId.get(options.toolId)?.find((candidate) => candidate.binding_id === binding.binding_id) ??
      options.claimsByToolId.get(options.toolId)?.[0] ??
      null

    if (isModelVisibleClaim(claim) && (!best || claimPriority(claim) > claimPriority(best.claim))) {
      best = { toolId: options.toolId, binding, claim }
    }

    for (const fallbackId of binding.fallback_binding_ids ?? []) {
      const fallback = options.candidates.find((candidate) => candidate.binding_id === fallbackId)
      if (fallback && !seen.has(fallback.binding_id)) {
        queue.push(fallback)
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
    return matchesSelector(binding.environment_selector ?? null, input)
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
        claimByBindingId,
        claimsByToolId,
      }),
    )
    .filter((resolved): resolved is ResolvedToolBinding => resolved != null)
}

export function resolveEffectiveToolSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceV1 {
  const resolvedBindings = resolveToolBindings(input)
  const toolIds = resolvedBindings.map((resolved) => resolved.toolId)
  const bindingIds = resolvedBindings.map((resolved) => resolved.binding.binding_id)
  const hiddenToolIds = input.claims
    .filter(
      (claim) =>
        claim.level === "hidden" ||
        claim.hidden_reason === "selector_mismatch" ||
        claim.exposed_to_model === false,
    )
    .map((claim) => claim.tool_id)

  return assertValid<EffectiveToolSurfaceV1>("effectiveToolSurface", {
    schema_version: "bb.effective_tool_surface.v1",
    surface_id: input.surfaceId,
    tool_ids: toolIds,
    binding_ids: bindingIds,
    hidden_tool_ids: hiddenToolIds,
    projection_profile_id: input.projectionProfileId ?? null,
    surface_hash: buildSurfaceHash(toolIds, bindingIds),
  })
}
