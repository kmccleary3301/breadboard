import { describe, expect, it } from "vitest"
import { evaluateBatchManifestSchema } from "../tools/assertions/batchManifestSchemaCheck.ts"

describe("batchManifestSchemaCheck", () => {
  it("passes for a minimally valid manifest", () => {
    const report = evaluateBatchManifestSchema({
      schemaVersion: 3,
      qcSpineVersion: "p2_v1",
      bundleKind: "maintenance-baseline",
      bundleAuthority: "maintenance",
      liveMode: "fresh-live",
      startedAtIso: "2026-04-14T00:00:00.000Z",
      finishedAtIso: "2026-04-14T00:00:05.000Z",
      artifactDir: "/tmp/batch",
      provenance: {
        runner: {
          rootDir: "/repo/tui_skeleton",
          repoRoot: "/repo",
          cwd: "/repo/tui_skeleton",
        },
      },
      cases: [
        {
          id: "spine_streaming_smoke",
          kind: "pty",
          description: "ok",
          outputs: {
            config: "/tmp/config.json",
            anomalies: "/tmp/anomalies.json",
            contractReport: "/tmp/contract_report.json",
            caseInfo: "/tmp/case_info.json",
          },
        },
      ],
    })
    expect(report.errors).toEqual([])
    expect(report.summary.caseCount).toBe(1)
  })

  it("fails when required fields are missing", () => {
    const report = evaluateBatchManifestSchema({ schemaVersion: 1, cases: [] })
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-schema-version")
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-qc-spine-version")
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-bundle-kind")
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-bundle-authority")
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-live-mode")
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-cases")
  })

  it("fails when a case reports anomalies", () => {
    const report = evaluateBatchManifestSchema({
      schemaVersion: 3,
      qcSpineVersion: "p2_v1",
      bundleKind: "maintenance-baseline",
      bundleAuthority: "maintenance",
      liveMode: "fresh-live",
      startedAtIso: "2026-04-14T00:00:00.000Z",
      finishedAtIso: "2026-04-14T00:00:05.000Z",
      artifactDir: "/tmp/batch",
      provenance: {
        runner: {
          rootDir: "/repo/tui_skeleton",
          repoRoot: "/repo",
          cwd: "/repo/tui_skeleton",
        },
      },
      cases: [
        {
          id: "streaming_resize_churn",
          kind: "emulator",
          description: "red",
          anomalyCount: 2,
          outputs: {
            config: "/tmp/config.json",
            anomalies: "/tmp/anomalies.json",
            contractReport: "/tmp/contract_report.json",
            caseInfo: "/tmp/case_info.json",
          },
        },
      ],
    })
    expect(report.errors.map((entry) => entry.id)).toContain("batch-manifest-case-0-anomalies-present")
  })
})
