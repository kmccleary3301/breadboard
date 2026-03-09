import { existsSync, readdirSync, readFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

import {
  assertValid,
  type EngineConformanceManifestV1,
} from "@breadboard/kernel-contracts"

const MODULE_DIR = dirname(fileURLToPath(import.meta.url))

export interface KernelConformanceSummary {
  schemaVersion: "bb.kernel_conformance_summary.v1"
  manifestRows: number
  comparatorClasses: string[]
  supportTiers: string[]
  fixtureFamilies: string[]
  fixtureCount: number
}

export function resolveRepoRoot(explicitRoot?: string): string {
  const candidates = [
    explicitRoot,
    process.env.BREADBOARD_REPO_ROOT,
    join(MODULE_DIR, "../../../../"),
    join(MODULE_DIR, "../../../../../"),
  ].filter((value): value is string => Boolean(value))
  for (const candidate of candidates) {
    const normalized = resolve(candidate)
    if (existsSync(join(normalized, "contracts", "kernel", "schemas"))) {
      return normalized
    }
  }
  throw new Error("Unable to resolve BreadBoard repo root for ts-kernel-core")
}

export function loadTrackedJson<T = unknown>(repoRelativePath: string, repoRoot?: string): T {
  const root = resolveRepoRoot(repoRoot)
  return JSON.parse(readFileSync(join(root, repoRelativePath), "utf8")) as T
}

export function loadEngineConformanceManifest(repoRoot?: string): EngineConformanceManifestV1 {
  const manifest = loadTrackedJson<unknown>("conformance/engine_fixtures/python_reference_manifest_v1.json", repoRoot)
  return assertValid<EngineConformanceManifestV1>("engineConformanceManifest", manifest)
}

export function loadKernelFixture<T = unknown>(fixtureRelativePath: string, repoRoot?: string): T {
  return loadTrackedJson<T>(join("conformance/engine_fixtures", fixtureRelativePath), repoRoot)
}

export function listKernelFixtureFamilies(repoRoot?: string): string[] {
  const root = resolveRepoRoot(repoRoot)
  const fixturesRoot = join(root, "conformance", "engine_fixtures")
  if (!existsSync(fixturesRoot)) {
    return []
  }
  return readdirSync(fixturesRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
}

export function buildConformanceSummary(repoRoot?: string): KernelConformanceSummary {
  const root = resolveRepoRoot(repoRoot)
  const manifest = loadEngineConformanceManifest(root)
  const fixtureFamilies = listKernelFixtureFamilies(root)
  let fixtureCount = 0
  for (const family of fixtureFamilies) {
    const familyRoot = join(root, "conformance", "engine_fixtures", family)
    fixtureCount += readdirSync(familyRoot, { withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name.endsWith(".json"))
      .length
  }
  return {
    schemaVersion: "bb.kernel_conformance_summary.v1",
    manifestRows: manifest.rows.length,
    comparatorClasses: Array.from(new Set(manifest.rows.map((row) => row.comparatorClass))).sort(),
    supportTiers: Array.from(new Set(manifest.rows.map((row) => row.supportTier))).sort(),
    fixtureFamilies,
    fixtureCount,
  }
}
