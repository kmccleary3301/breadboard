import { describe, expect, it } from "vitest"
import { evaluateLiveProvenance } from "../tools/assertions/liveProvenanceCheck.ts"

describe("liveProvenanceCheck", () => {
  it("passes for matching served revision", () => {
    const report = evaluateLiveProvenance({
      liveMode: "fresh-live",
      expected: {
        repoRoot: "/repo",
        commit: "abc123",
        branch: "main",
        dirty: false,
      },
      observed: {
        status: {
          served_revision: {
            repo_root: "/repo",
            commit: "abc123",
            branch: "main",
            dirty: false,
          },
        },
      },
    })

    expect(report.errors).toEqual([])
    expect(report.summary.matched).toBe(true)
  })

  it("fails on commit mismatch by default", () => {
    const report = evaluateLiveProvenance({
      liveMode: "fresh-live",
      expected: {
        repoRoot: "/repo",
        commit: "abc123",
        branch: "main",
        dirty: false,
      },
      observed: {
        status: {
          served_revision: {
            repo_root: "/repo",
            commit: "def456",
            branch: "main",
            dirty: false,
          },
        },
      },
    })

    expect(report.errors.map((entry) => entry.id)).toContain("live-provenance-commit-mismatch")
    expect(report.summary.matched).toBe(false)
  })

  it("fails when served revision is missing", () => {
    const report = evaluateLiveProvenance({
      liveMode: "warm-live",
      expected: {
        repoRoot: "/repo",
        commit: "abc123",
        dirty: false,
      },
      observed: {
        health: {},
        status: {},
      },
    })

    expect(report.errors.map((entry) => entry.id)).toContain("live-provenance-missing-served-revision")
  })

  it("warns when health and status disagree", () => {
    const report = evaluateLiveProvenance({
      liveMode: "warm-live",
      expected: {
        repoRoot: "/repo",
        commit: "abc123",
        branch: "main",
        dirty: false,
      },
      observed: {
        health: {
          served_revision: {
            repo_root: "/repo",
            commit: "abc123",
            branch: "main",
            dirty: false,
          },
        },
        status: {
          served_revision: {
            repo_root: "/repo-alt",
            commit: "abc123",
            branch: "main",
            dirty: false,
          },
        },
      },
    })

    expect(report.warnings.map((entry) => entry.id)).toContain("live-provenance-health-status-disagree")
  })
})
