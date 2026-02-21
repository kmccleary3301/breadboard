import type { ToolLogEntry } from "../../../types.js"

export type ToolRenderDensity = "auto" | "compact" | "expanded"

export interface ToolRenderPolicy {
  readonly mode?: ToolRenderDensity
  readonly collapseThreshold?: number
  readonly collapseHead?: number
  readonly collapseTail?: number
}

export interface ToolRenderPolicyProvider {
  resolve: (entry: ToolLogEntry) => ToolRenderPolicy | null
}

export interface ToolRenderPolicyResolved {
  readonly mode: ToolRenderDensity
  readonly collapseThreshold: number
  readonly collapseHead: number
  readonly collapseTail: number
}

export const DEFAULT_TOOL_RENDER_POLICY_PROVIDER: ToolRenderPolicyProvider = {
  resolve: () => null,
}

const clampPositiveInt = (value: number, fallback: number): number => {
  if (!Number.isFinite(value)) return fallback
  const normalized = Math.floor(value)
  return normalized > 0 ? normalized : fallback
}

export const resolveToolRenderPolicy = (
  entry: ToolLogEntry,
  defaults: {
    collapseThreshold: number
    collapseHead: number
    collapseTail: number
  },
  provider: ToolRenderPolicyProvider,
): ToolRenderPolicyResolved => {
  const resolved = provider.resolve(entry)
  const mode = resolved?.mode ?? "auto"
  return {
    mode,
    collapseThreshold: clampPositiveInt(resolved?.collapseThreshold ?? defaults.collapseThreshold, defaults.collapseThreshold),
    collapseHead: clampPositiveInt(resolved?.collapseHead ?? defaults.collapseHead, defaults.collapseHead),
    collapseTail: clampPositiveInt(resolved?.collapseTail ?? defaults.collapseTail, defaults.collapseTail),
  }
}
