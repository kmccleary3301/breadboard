import { DEFAULT_RESOLVED_TUI_CONFIG } from "../../../../tui_config/presets.js"
import type { ResolvedTuiConfig } from "../../../../tui_config/types.js"

export type DiffRenderPalette = {
  readonly addLineBg: string
  readonly deleteLineBg: string
  readonly hunkLineBg: string
  readonly addInlineBg: string
  readonly deleteInlineBg: string
  readonly addText: string
  readonly deleteText: string
  readonly hunkText: string
  readonly metaText: string
}

export type DiffRenderStyle = {
  readonly previewMaxLines: number
  readonly maxTokenizedLines: number
  readonly colors: DiffRenderPalette
}

const normalizePositiveInt = (value: number | undefined, fallback: number): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback
  const floored = Math.floor(value)
  return floored > 0 ? floored : fallback
}

const defaultDiff = DEFAULT_RESOLVED_TUI_CONFIG.diff

export const DEFAULT_DIFF_RENDER_STYLE: DiffRenderStyle = {
  previewMaxLines: defaultDiff.previewMaxLines,
  maxTokenizedLines: defaultDiff.maxTokenizedLines,
  colors: {
    addLineBg: defaultDiff.colors.addLineBg,
    deleteLineBg: defaultDiff.colors.deleteLineBg,
    hunkLineBg: defaultDiff.colors.hunkLineBg,
    addInlineBg: defaultDiff.colors.addInlineBg,
    deleteInlineBg: defaultDiff.colors.deleteInlineBg,
    addText: defaultDiff.colors.addText,
    deleteText: defaultDiff.colors.deleteText,
    hunkText: defaultDiff.colors.hunkText,
    metaText: defaultDiff.colors.metaText,
  },
}

export const resolveDiffRenderStyle = (config?: ResolvedTuiConfig | null): DiffRenderStyle => {
  const diff = config?.diff
  if (!diff) return DEFAULT_DIFF_RENDER_STYLE
  return {
    previewMaxLines: normalizePositiveInt(diff.previewMaxLines, DEFAULT_DIFF_RENDER_STYLE.previewMaxLines),
    maxTokenizedLines: normalizePositiveInt(diff.maxTokenizedLines, DEFAULT_DIFF_RENDER_STYLE.maxTokenizedLines),
    colors: {
      addLineBg: diff.colors.addLineBg || DEFAULT_DIFF_RENDER_STYLE.colors.addLineBg,
      deleteLineBg: diff.colors.deleteLineBg || DEFAULT_DIFF_RENDER_STYLE.colors.deleteLineBg,
      hunkLineBg: diff.colors.hunkLineBg || DEFAULT_DIFF_RENDER_STYLE.colors.hunkLineBg,
      addInlineBg: diff.colors.addInlineBg || DEFAULT_DIFF_RENDER_STYLE.colors.addInlineBg,
      deleteInlineBg: diff.colors.deleteInlineBg || DEFAULT_DIFF_RENDER_STYLE.colors.deleteInlineBg,
      addText: diff.colors.addText || DEFAULT_DIFF_RENDER_STYLE.colors.addText,
      deleteText: diff.colors.deleteText || DEFAULT_DIFF_RENDER_STYLE.colors.deleteText,
      hunkText: diff.colors.hunkText || DEFAULT_DIFF_RENDER_STYLE.colors.hunkText,
      metaText: diff.colors.metaText || DEFAULT_DIFF_RENDER_STYLE.colors.metaText,
    },
  }
}

