import { promises as fs } from "node:fs"
import path from "node:path"

export interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

export interface BatchManifestContractReport {
  readonly summary: {
    readonly schemaVersion: number | null
    readonly qcSpineVersion: string | null
    readonly bundleKind: string | null
    readonly bundleAuthority: string | null
    readonly liveMode: string | null
    readonly caseCount: number
    readonly errorsCount: number
    readonly warningsCount: number
  }
  readonly errors: LayoutAnomaly[]
  readonly warnings: LayoutAnomaly[]
}

const readJson = async <T>(filePath: string): Promise<T> =>
  JSON.parse(await fs.readFile(filePath, "utf8")) as T

const expectString = (errors: LayoutAnomaly[], value: unknown, id: string, message: string) => {
  if (typeof value !== "string" || value.trim() === "") {
    errors.push({ id, message })
  }
}

const expectObject = (errors: LayoutAnomaly[], value: unknown, id: string, message: string): Record<string, unknown> | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    errors.push({ id, message })
    return null
  }
  return value as Record<string, unknown>
}

export const evaluateBatchManifestSchema = (manifest: Record<string, unknown>): BatchManifestContractReport => {
  const errors: LayoutAnomaly[] = []
  const warnings: LayoutAnomaly[] = []

  const schemaVersion = typeof manifest.schemaVersion === "number" ? manifest.schemaVersion : null
  const qcSpineVersion = typeof manifest.qcSpineVersion === "string" ? manifest.qcSpineVersion : null
  const bundleKind = typeof manifest.bundleKind === "string" ? manifest.bundleKind : null
  const bundleAuthority = typeof manifest.bundleAuthority === "string" ? manifest.bundleAuthority : null
  const liveMode = typeof manifest.liveMode === "string" ? manifest.liveMode : null
  const cases = Array.isArray(manifest.cases) ? (manifest.cases as Record<string, unknown>[]) : []

  if (schemaVersion !== 3) {
    errors.push({ id: "batch-manifest-schema-version", message: `expected schemaVersion=3, got ${String(manifest.schemaVersion)}` })
  }
  if (!qcSpineVersion) {
    errors.push({ id: "batch-manifest-qc-spine-version", message: "missing qcSpineVersion" })
  }
  expectString(errors, manifest.bundleKind, "batch-manifest-bundle-kind", "missing bundleKind")
  expectString(errors, manifest.bundleAuthority, "batch-manifest-bundle-authority", "missing bundleAuthority")
  expectString(errors, manifest.liveMode, "batch-manifest-live-mode", "missing liveMode")
  expectString(errors, manifest.startedAtIso, "batch-manifest-started-at-iso", "missing startedAtIso")
  expectString(errors, manifest.finishedAtIso, "batch-manifest-finished-at-iso", "missing finishedAtIso")
  expectString(errors, manifest.artifactDir, "batch-manifest-artifact-dir", "missing artifactDir")

  const provenance = expectObject(errors, manifest.provenance, "batch-manifest-provenance", "missing provenance object")
  const runner = provenance ? expectObject(errors, provenance.runner, "batch-manifest-runner", "missing provenance.runner") : null
  if (runner) {
    expectString(errors, runner.rootDir, "batch-manifest-runner-root-dir", "missing provenance.runner.rootDir")
    expectString(errors, runner.repoRoot, "batch-manifest-runner-repo-root", "missing provenance.runner.repoRoot")
    expectString(errors, runner.cwd, "batch-manifest-runner-cwd", "missing provenance.runner.cwd")
  }

  if (!Array.isArray(manifest.cases) || cases.length === 0) {
    errors.push({ id: "batch-manifest-cases", message: "manifest.cases must be a non-empty array" })
  }

  for (const [index, caseEntry] of cases.entries()) {
    expectString(errors, caseEntry.id, `batch-manifest-case-${index}-id`, `case ${index} missing id`)
    expectString(errors, caseEntry.kind, `batch-manifest-case-${index}-kind`, `case ${index} missing kind`)
    expectString(errors, caseEntry.description, `batch-manifest-case-${index}-description`, `case ${index} missing description`)
    const outputs = expectObject(errors, caseEntry.outputs, `batch-manifest-case-${index}-outputs`, `case ${index} missing outputs object`)
    if (outputs) {
      expectString(errors, outputs.config, `batch-manifest-case-${index}-outputs-config`, `case ${index} missing outputs.config`)
      expectString(errors, outputs.anomalies, `batch-manifest-case-${index}-outputs-anomalies`, `case ${index} missing outputs.anomalies`)
      expectString(errors, outputs.contractReport, `batch-manifest-case-${index}-outputs-contract-report`, `case ${index} missing outputs.contractReport`)
      expectString(errors, outputs.caseInfo, `batch-manifest-case-${index}-outputs-case-info`, `case ${index} missing outputs.caseInfo`)
    }
    const anomalyCount = typeof caseEntry.anomalyCount === "number" ? caseEntry.anomalyCount : 0
    if (anomalyCount > 0) {
      errors.push({
        id: `batch-manifest-case-${index}-anomalies-present`,
        message: `case ${String(caseEntry.id ?? index)} reported ${anomalyCount} anomaly/anomalies`,
      })
    }
  }

  return {
    summary: {
      schemaVersion,
      qcSpineVersion,
      bundleKind,
      bundleAuthority,
      liveMode,
      caseCount: cases.length,
      errorsCount: errors.length,
      warningsCount: warnings.length,
    },
    errors,
    warnings,
  }
}

export const evaluateBatchManifestSchemaFromFile = async (manifestPath: string): Promise<BatchManifestContractReport> => {
  const manifest = await readJson<Record<string, unknown>>(manifestPath)
  return evaluateBatchManifestSchema(manifest)
}

const parseArgs = (): { manifestPath: string } => {
  const args = process.argv.slice(2)
  let manifestPath: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--manifest") manifestPath = args[++i]
  }
  if (!manifestPath) throw new Error("--manifest is required")
  return { manifestPath: path.resolve(manifestPath) }
}

const run = async () => {
  const { manifestPath } = parseArgs()
  const report = await evaluateBatchManifestSchemaFromFile(manifestPath)
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
