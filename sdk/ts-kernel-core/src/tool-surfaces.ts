import { createHash } from "node:crypto"

import {
  assertValid,
  type EffectiveToolSurfaceV1,
  type EnvironmentSelectorV1,
  type ToolBindingV1,
  type ToolSupportClaimV1,
} from "@breadboard/kernel-contracts"

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

export function resolveEffectiveToolSurface(input: EffectiveToolSurfaceResolutionInput): EffectiveToolSurfaceV1 {
  const visibleClaims = input.claims.filter((claim) => claim.level !== "hidden")
  const claimByBinding = new Map(
    visibleClaims
      .filter((claim) => typeof claim.binding_id === "string" && claim.binding_id.length > 0)
      .map((claim) => [claim.binding_id!, claim]),
  )
  const activeBindings = input.bindings.filter((binding) => {
    if (!matchesSelector(binding.environment_selector ?? null, input)) {
      return false
    }
    const claim = claimByBinding.get(binding.binding_id)
    return claim != null || visibleClaims.some((visible) => visible.tool_id === binding.tool_id)
  })
  const toolIds = [...new Set(activeBindings.map((binding) => binding.tool_id))]
  const bindingIds = activeBindings.map((binding) => binding.binding_id)
  const hiddenToolIds = input.claims
    .filter((claim) => claim.level === "hidden" || claim.hidden_reason === "selector_mismatch")
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
