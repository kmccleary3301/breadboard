import { homedir } from "node:os"
import path from "node:path"
import { promises as fs } from "node:fs"
import { createHash } from "node:crypto"
import { spawn } from "node:child_process"
import { Readable } from "node:stream"

export interface EngineBundleAsset {
  readonly platform: string
  readonly arch: string
  readonly url: string
  readonly sha256?: string
  readonly entry?: string
}

export interface EngineBundleManifest {
  readonly version: string
  readonly created_at?: string
  readonly min_cli_version?: string
  readonly protocol_version?: string
  readonly assets: EngineBundleAsset[]
}

export interface EngineBundleInfo {
  readonly version: string
  readonly platform: string
  readonly arch: string
  readonly root: string
  readonly entry: string
  readonly minCliVersion?: string
  readonly protocolVersion?: string
}

const CACHE_ROOT = path.join(homedir(), ".breadboard", "engine")
const META_FILENAME = "bundle.json"

const ensureDir = async (dir: string) => {
  await fs.mkdir(dir, { recursive: true })
}

const readJson = async <T>(filePath: string): Promise<T | null> => {
  try {
    const raw = await fs.readFile(filePath, "utf8")
    return JSON.parse(raw) as T
  } catch {
    return null
  }
}

const writeJson = async (filePath: string, payload: unknown) => {
  await ensureDir(path.dirname(filePath))
  await fs.writeFile(filePath, JSON.stringify(payload, null, 2), "utf8")
}

export const getEngineCacheRoot = () => CACHE_ROOT

export const listCachedBundles = async (): Promise<EngineBundleInfo[]> => {
  try {
    const entries = await fs.readdir(CACHE_ROOT, { withFileTypes: true })
    const results: EngineBundleInfo[] = []
    for (const entry of entries) {
      if (!entry.isDirectory()) continue
      const meta = await readJson<EngineBundleInfo>(path.join(CACHE_ROOT, entry.name, META_FILENAME))
      if (meta && meta.entry) {
        results.push(meta)
      }
    }
    return results
  } catch {
    return []
  }
}

export const resolveCachedBundle = async (version: string): Promise<EngineBundleInfo | null> => {
  const metaPath = path.join(CACHE_ROOT, version, META_FILENAME)
  const meta = await readJson<EngineBundleInfo>(metaPath)
  if (!meta) return null
  const entryPath = path.isAbsolute(meta.entry) ? meta.entry : path.join(meta.root, meta.entry)
  try {
    await fs.access(entryPath)
    return { ...meta, entry: entryPath }
  } catch {
    return null
  }
}

const detectEntry = async (root: string, asset: EngineBundleAsset): Promise<string> => {
  if (asset.entry) {
    return asset.entry
  }
  const candidates = [
    "breadboard-engine",
    "breadboard_engine",
    "bin/breadboard-engine",
    "bin/breadboard_engine",
    "breadboard-engine.exe",
    "breadboard_engine.exe",
  ]
  for (const candidate of candidates) {
    const full = path.join(root, candidate)
    try {
      await fs.access(full)
      return candidate
    } catch {
      // keep searching
    }
  }
  throw new Error("Unable to locate engine binary in bundle.")
}

const sha256 = async (filePath: string): Promise<string> => {
  const hash = createHash("sha256")
  const handle = await fs.open(filePath, "r")
  try {
    const stream = handle.createReadStream()
    await new Promise<void>((resolve, reject) => {
      stream.on("data", (chunk) => hash.update(chunk))
      stream.on("error", reject)
      stream.on("end", () => resolve())
    })
  } finally {
    await handle.close()
  }
  return hash.digest("hex")
}

const extractArchive = async (archivePath: string, dest: string): Promise<void> => {
  await ensureDir(dest)
  if (archivePath.endsWith(".tar.gz") || archivePath.endsWith(".tgz")) {
    await new Promise<void>((resolve, reject) => {
      const child = spawn("tar", ["-xzf", archivePath, "-C", dest])
      child.on("error", reject)
      child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`tar exited ${code}`))))
    })
    return
  }
  if (archivePath.endsWith(".zip")) {
    await new Promise<void>((resolve, reject) => {
      const child = spawn("unzip", ["-o", archivePath, "-d", dest])
      child.on("error", reject)
      child.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`unzip exited ${code}`))))
    })
    return
  }
  throw new Error(`Unknown archive format: ${archivePath}`)
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const fetchWithRetries = async (url: string, retries = 2, delayMs = 500): Promise<Response> => {
  let attempt = 0
  let lastError: Error | null = null
  while (attempt <= retries) {
    try {
      const response = await fetch(url)
      if (response.ok) return response
      lastError = new Error(`Request failed (${response.status})`)
    } catch (error) {
      lastError = error as Error
    }
    if (attempt < retries) {
      await sleep(delayMs * Math.pow(2, attempt))
    }
    attempt += 1
  }
  throw lastError ?? new Error("Request failed")
}

const fetchManifest = async (url: string, retries?: number): Promise<EngineBundleManifest> => {
  const response = await fetchWithRetries(url, retries)
  return (await response.json()) as EngineBundleManifest
}

const selectAsset = (manifest: EngineBundleManifest): EngineBundleAsset => {
  const platform = process.platform
  const arch = process.arch
  const match = manifest.assets.find((asset) => asset.platform === platform && asset.arch === arch)
  if (!match) {
    throw new Error(`No engine bundle for ${platform}/${arch}`)
  }
  return match
}

const parseSemver = (value: string): [number, number, number] | null => {
  const parts = value.split(".").map((part) => Number(part))
  if (parts.length < 1) return null
  const [major, minor = 0, patch = 0] = parts
  if (![major, minor, patch].every((num) => Number.isFinite(num))) return null
  return [major, minor, patch]
}

const compareSemver = (lhs: string, rhs: string): number => {
  const a = parseSemver(lhs)
  const b = parseSemver(rhs)
  if (!a || !b) return lhs.localeCompare(rhs)
  for (let i = 0; i < 3; i += 1) {
    if (a[i] < b[i]) return -1
    if (a[i] > b[i]) return 1
  }
  return 0
}

export const downloadBundle = async (
  manifestUrl: string,
  options: { version?: string; cliVersion?: string; protocolVersion?: string; retries?: number } = {},
): Promise<EngineBundleInfo> => {
  const manifest = await fetchManifest(manifestUrl, options.retries)
  if (options.version && manifest.version !== options.version) {
    throw new Error(`Manifest version ${manifest.version} does not match requested ${options.version}`)
  }
  if (manifest.min_cli_version && options.cliVersion) {
    if (compareSemver(options.cliVersion, manifest.min_cli_version) < 0) {
      throw new Error(
        `CLI ${options.cliVersion} is older than required ${manifest.min_cli_version} for engine bundle ${manifest.version}`,
      )
    }
  }
  if (manifest.protocol_version && options.protocolVersion && manifest.protocol_version !== options.protocolVersion) {
    const strict = process.env.BREADBOARD_PROTOCOL_STRICT === "1"
    const message = `Engine bundle protocol ${manifest.protocol_version} does not match CLI protocol ${options.protocolVersion}`
    if (strict) {
      throw new Error(message)
    }
    console.warn(`[engine] ${message}`)
  }
  const asset = selectAsset(manifest)
  const versionDir = path.join(CACHE_ROOT, manifest.version)
  await ensureDir(versionDir)
  const archivePath = path.join(versionDir, "engine_bundle")

  const response = await fetchWithRetries(asset.url, options.retries)
  if (!response.body) {
    throw new Error("Bundle download failed (empty body)")
  }
  const file = await fs.open(archivePath, "w")
  try {
    const writer = file.createWriteStream()
    const readable = Readable.fromWeb(response.body as any)
    await new Promise<void>((resolve, reject) => {
      readable.pipe(writer)
      readable.on("error", reject)
      writer.on("error", reject)
      writer.on("finish", resolve)
    })
  } finally {
    await file.close()
  }

  if (asset.sha256) {
    const digest = await sha256(archivePath)
    if (digest !== asset.sha256) {
      throw new Error("Bundle checksum mismatch.")
    }
  }

  const extractRoot = path.join(versionDir, "bundle")
  await extractArchive(archivePath, extractRoot)
  const entry = await detectEntry(extractRoot, asset)
  const info: EngineBundleInfo = {
    version: manifest.version,
    platform: asset.platform,
    arch: asset.arch,
    root: extractRoot,
    entry,
    minCliVersion: manifest.min_cli_version,
    protocolVersion: manifest.protocol_version,
  }
  await writeJson(path.join(versionDir, META_FILENAME), info)
  return info
}
