import path from "node:path"

export interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

export interface GitRevisionInfo {
  readonly repoRoot?: string | null
  readonly commit?: string | null
  readonly branch?: string | null
  readonly dirty?: boolean | null
}

export interface ServedRevisionInfo {
  readonly repo_root?: string | null
  readonly commit?: string | null
  readonly branch?: string | null
  readonly dirty?: boolean | null
}

export interface LiveBridgeProvenance {
  readonly health?: {
    readonly served_revision?: ServedRevisionInfo | null
  } | null
  readonly status?: {
    readonly served_revision?: ServedRevisionInfo | null
  } | null
}

export interface LiveProvenanceReport {
  readonly summary: {
    readonly liveMode: "fresh-live" | "warm-live" | "not-live"
    readonly checked: boolean
    readonly matched: boolean
    readonly mismatchOverride: boolean
  }
  readonly expected: GitRevisionInfo
  readonly observed: {
    readonly selected: ServedRevisionInfo | null
    readonly health: ServedRevisionInfo | null
    readonly status: ServedRevisionInfo | null
  }
  readonly errors: LayoutAnomaly[]
  readonly warnings: LayoutAnomaly[]
}

const normalizeRoot = (value: string | null | undefined): string | null => {
  if (!value || value.trim().length === 0) return null
  return path.resolve(value)
}

const sameOptional = (left: string | null | undefined, right: string | null | undefined): boolean => {
  if (!left || !right) return true
  return left === right
}

const sameOptionalBoolean = (left: boolean | null | undefined, right: boolean | null | undefined): boolean => {
  if (left === null || left === undefined || right === null || right === undefined) return true
  return left === right
}

const selectServedRevision = (provenance: LiveBridgeProvenance | null | undefined): {
  readonly selected: ServedRevisionInfo | null
  readonly health: ServedRevisionInfo | null
  readonly status: ServedRevisionInfo | null
} => {
  const health = provenance?.health?.served_revision ?? null
  const status = provenance?.status?.served_revision ?? null
  const selected =
    status?.commit || status?.repo_root || status?.branch || typeof status?.dirty === "boolean"
      ? status
      : health
  return {
    selected: selected ?? null,
    health,
    status,
  }
}

export const evaluateLiveProvenance = (input: {
  readonly liveMode: "fresh-live" | "warm-live" | "not-live"
  readonly expected: GitRevisionInfo
  readonly observed: LiveBridgeProvenance | null
  readonly mismatchOverride?: boolean
}): LiveProvenanceReport => {
  const errors: LayoutAnomaly[] = []
  const warnings: LayoutAnomaly[] = []
  const observed = selectServedRevision(input.observed)
  const expected = input.expected
  const mismatchOverride = input.mismatchOverride === true

  if (input.liveMode === "not-live") {
    return {
      summary: {
        liveMode: input.liveMode,
        checked: false,
        matched: true,
        mismatchOverride,
      },
      expected,
      observed,
      errors,
      warnings,
    }
  }

  const selected = observed.selected
  if (!selected) {
    errors.push({
      id: "live-provenance-missing-served-revision",
      message: "live provenance did not expose served_revision on /health or /status",
    })
  }

  if (observed.health && observed.status) {
    const healthRoot = normalizeRoot(observed.health.repo_root)
    const statusRoot = normalizeRoot(observed.status.repo_root)
    if (!sameOptional(observed.health.commit, observed.status.commit) || !sameOptional(statusRoot, healthRoot)) {
      warnings.push({
        id: "live-provenance-health-status-disagree",
        message: "health and status served_revision disagree; status will be treated as primary",
      })
    }
  }

  if (selected) {
    const expectedRoot = normalizeRoot(expected.repoRoot)
    const observedRoot = normalizeRoot(selected.repo_root)

    if (expected.commit && selected.commit && expected.commit !== selected.commit) {
      errors.push({
        id: "live-provenance-commit-mismatch",
        message: `served commit ${selected.commit} does not match local commit ${expected.commit}`,
      })
    }

    if (expectedRoot && observedRoot && expectedRoot !== observedRoot) {
      errors.push({
        id: "live-provenance-root-mismatch",
        message: `served repo root ${observedRoot} does not match local repo root ${expectedRoot}`,
      })
    }

    if (!sameOptionalBoolean(expected.dirty, selected.dirty)) {
      errors.push({
        id: "live-provenance-dirty-mismatch",
        message: `served dirty=${String(selected.dirty)} does not match local dirty=${String(expected.dirty)}`,
      })
    }

    if (expected.branch && selected.branch && expected.branch !== selected.branch) {
      warnings.push({
        id: "live-provenance-branch-mismatch",
        message: `served branch ${selected.branch} does not match local branch ${expected.branch}`,
      })
    }
  }

  return {
    summary: {
      liveMode: input.liveMode,
      checked: true,
      matched: errors.length === 0,
      mismatchOverride,
    },
    expected,
    observed,
    errors,
    warnings,
  }
}
