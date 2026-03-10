import dotenv from "dotenv"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

export interface RuntimeIntegrityReport {
  readonly projectRoot: string
  readonly repoRoot: string
  readonly engineRoot: string
  readonly envFilesLoaded: readonly string[]
  readonly checkedFiles: readonly string[]
  readonly missingFiles: readonly string[]
}

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url))

const findUpward = (startDir: string, relativePath: string): string | null => {
  let current = path.resolve(startDir)
  for (;;) {
    const candidate = path.join(current, relativePath)
    if (fs.existsSync(candidate)) return current
    const parent = path.dirname(current)
    if (parent === current) return null
    current = parent
  }
}

export const resolveProjectRoot = (): string => {
  const hit = findUpward(MODULE_DIR, "package.json")
  if (!hit) throw new Error(`Unable to resolve BreadBoard TUI project root from ${MODULE_DIR}`)
  return hit
}

export const resolveRepoRoot = (): string => {
  const explicit = process.env.BREADBOARD_REPO_ROOT?.trim()
  if (explicit) return path.resolve(explicit)
  const projectRoot = resolveProjectRoot()
  const repoRoot = path.resolve(projectRoot, "..")
  if (!fs.existsSync(path.join(repoRoot, "agentic_coder_prototype"))) {
    throw new Error(`Unable to resolve BreadBoard repo root from ${projectRoot}`)
  }
  return repoRoot
}

export const resolveEngineRoot = (): string => {
  const explicit = process.env.BREADBOARD_ENGINE_ROOT?.trim()
  if (explicit) return path.resolve(explicit)
  return resolveRepoRoot()
}

export const getCriticalFiles = (options: { includeSource?: boolean; includeDist?: boolean } = {}): string[] => {
  const projectRoot = resolveProjectRoot()
  const repoRoot = resolveRepoRoot()
  const files = [
    path.join(projectRoot, "package.json"),
    path.join(repoRoot, "agentic_coder_prototype", "api", "cli_bridge", "server.py"),
    path.join(repoRoot, "agentic_coder_prototype", "utils", "safe_delete.py"),
  ]
  if (options.includeSource !== false) {
    files.push(path.join(projectRoot, "src", "main.ts"))
  }
  if (options.includeDist) {
    files.push(path.join(projectRoot, "dist", "main.js"))
  }
  return files
}

export const collectRuntimeIntegrityReport = (
  options: { includeSource?: boolean; includeDist?: boolean } = {},
): RuntimeIntegrityReport => {
  const projectRoot = resolveProjectRoot()
  const repoRoot = resolveRepoRoot()
  const engineRoot = resolveEngineRoot()
  const checkedFiles = getCriticalFiles(options)
  const missingFiles = checkedFiles.filter((candidate) => !fs.existsSync(candidate))
  return {
    projectRoot,
    repoRoot,
    engineRoot,
    envFilesLoaded: [],
    checkedFiles,
    missingFiles,
  }
}

let envLoaded = false
let envFilesLoaded: string[] = []

export const loadRepoDotenv = (): string[] => {
  if (envLoaded) return envFilesLoaded
  const repoRoot = resolveRepoRoot()
  const candidates = [path.join(repoRoot, ".env"), path.join(repoRoot, ".env.local")]
  envFilesLoaded = []
  for (const candidate of candidates) {
    if (!fs.existsSync(candidate)) continue
    dotenv.config({ path: candidate, override: false })
    envFilesLoaded.push(candidate)
  }
  envLoaded = true
  return envFilesLoaded
}

export const assertRuntimeIntegrity = (options: { includeSource?: boolean; includeDist?: boolean } = {}): RuntimeIntegrityReport => {
  const report = collectRuntimeIntegrityReport(options)
  const loaded = loadRepoDotenv()
  const finalReport: RuntimeIntegrityReport = {
    ...report,
    envFilesLoaded: loaded,
  }
  if (finalReport.missingFiles.length > 0) {
    throw new Error(
      `BreadBoard runtime integrity check failed. Missing: ${finalReport.missingFiles.join(", ")}`,
    )
  }
  return finalReport
}
