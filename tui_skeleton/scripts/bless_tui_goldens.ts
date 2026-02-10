import { promises as fs } from "node:fs"
import path from "node:path"
import yaml from "yaml"

type RenderOptions = {
  include_header?: boolean
  include_status?: boolean
  include_hints?: boolean
  include_model_menu?: boolean
  colors?: boolean
  ascii_only?: boolean
  max_width?: number
  color_mode?: string
}

type NormalizeOptions = {
  timestamps?: boolean
  tokens?: boolean
  paths?: boolean
  status?: boolean
}

type ScenarioConfig = {
  id: string
  render?: RenderOptions
  normalize?: NormalizeOptions
}

type Manifest = {
  version: number
  defaults?: {
    render?: RenderOptions
    normalize?: NormalizeOptions
  }
  scenarios: ScenarioConfig[]
}

type CliOptions = {
  manifestPath: string
  sourceRoot: string
  blessedRoot: string
}

const parseArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  let manifestPath = "misc/tui_goldens/manifests/tui_goldens.yaml"
  let sourceRoot = ""
  let blessedRoot = "misc/tui_goldens/scenarios"

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--manifest":
        manifestPath = args[++i] ?? manifestPath
        break
      case "--source":
        sourceRoot = args[++i] ?? ""
        break
      case "--blessed-root":
        blessedRoot = args[++i] ?? blessedRoot
        break
      default:
        break
    }
  }

  if (!sourceRoot) {
    throw new Error("Usage: tsx scripts/bless_tui_goldens.ts --source <dir> [--manifest <path>]")
  }

  return { manifestPath, sourceRoot, blessedRoot }
}

const normalizePath = (value: string, baseDir: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(baseDir, value)
}

const readManifest = async (manifestPath: string): Promise<Manifest> => {
  const raw = await fs.readFile(manifestPath, "utf8")
  const parsed = yaml.parse(raw) as Manifest
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.scenarios)) {
    throw new Error(`Invalid manifest: ${manifestPath}`)
  }
  return parsed
}

const main = async () => {
  const options = parseArgs()
  const manifestPath = normalizePath(options.manifestPath, process.cwd())
  const manifest = await readManifest(manifestPath)
  const renderDefaults = manifest.defaults?.render ?? {}
  const normalizeDefaults = manifest.defaults?.normalize ?? {}
  const sourceRoot = normalizePath(options.sourceRoot, process.cwd())
  const blessedRoot = normalizePath(options.blessedRoot, process.cwd())

  let gitSha = ""
  try {
    const { execSync } = await import("node:child_process")
    gitSha = execSync("git rev-parse HEAD", { encoding: "utf8" }).trim()
  } catch {
    gitSha = ""
  }

  for (const scenario of manifest.scenarios) {
    const sourcePath = path.join(sourceRoot, scenario.id, "render.txt")
    const sourceGridPath = path.join(sourceRoot, scenario.id, "frame.grid.json")
    const destDir = path.join(blessedRoot, scenario.id, "blessed")
    await fs.mkdir(destDir, { recursive: true })
    const destPath = path.join(destDir, "render.txt")
    await fs.access(sourcePath)
    await fs.copyFile(sourcePath, destPath)
    try {
      await fs.access(sourceGridPath)
      await fs.copyFile(sourceGridPath, path.join(destDir, "frame.grid.json"))
    } catch {
      // grid artifact optional
    }
    const meta = {
      id: scenario.id,
      blessed_at: new Date().toISOString(),
      git_sha: gitSha || null,
      manifest_version: manifest.version,
      render: { ...renderDefaults, ...(scenario.render ?? {}) },
      normalize: { ...normalizeDefaults, ...(scenario.normalize ?? {}) },
      source: sourcePath,
      source_grid: sourceGridPath,
    }
    await fs.writeFile(path.join(destDir, "meta.json"), `${JSON.stringify(meta, null, 2)}\n`, "utf8")
    console.log(`[bless] ${scenario.id} -> ${destPath}`)
  }
}

main().catch((error) => {
  console.error("[bless_tui_goldens] failed:", error)
  process.exit(1)
})
