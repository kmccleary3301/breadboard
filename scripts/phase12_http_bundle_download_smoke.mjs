#!/usr/bin/env node
import { createHash } from "node:crypto"
import { spawnSync } from "node:child_process"
import { promises as fs } from "node:fs"
import http from "node:http"
import os from "node:os"
import path from "node:path"
import { fileURLToPath, pathToFileURL } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(here, "..")
const tuiDir = path.join(rootDir, "tui_skeleton")
const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm"

const run = (command, args, options = {}) => {
  const result = spawnSync(command, args, { stdio: "inherit", ...options })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")} (exit ${result.status})`)
  }
}

const sha256File = async (filePath) => {
  const hash = createHash("sha256")
  const handle = await fs.open(filePath, "r")
  try {
    const stream = handle.createReadStream()
    await new Promise((resolve, reject) => {
      stream.on("data", (chunk) => hash.update(chunk))
      stream.on("error", reject)
      stream.on("end", resolve)
    })
  } finally {
    await handle.close()
  }
  return hash.digest("hex")
}

const rimraf = async (target) => {
  try {
    await fs.rm(target, { recursive: true, force: true })
  } catch {
    // ignore
  }
}

const main = async () => {
  // Ensure the TUI dist exists so we can import engineBundles from compiled JS.
  const distBundles = path.join(tuiDir, "dist", "engine", "engineBundles.js")
  const skipBuild = process.env.BREADBOARD_SKIP_TUI_BUILD === "1"
  const hasDist = await fs
    .stat(distBundles)
    .then(() => true)
    .catch(() => false)
  if (!skipBuild && !hasDist) {
    run(npmCmd, ["run", "build"], {
      cwd: tuiDir,
      env: { ...process.env, BREADBOARD_SKIP_LOCAL_BIN_INSTALL: "1", BREADBOARD_INSTALL_QUIET: "1" },
      shell: process.platform === "win32",
    })
  }

  const tmp = await fs.mkdtemp(path.join(os.tmpdir(), "bb-http-bundle-smoke-"))
  const staging = path.join(tmp, "bundle")
  await fs.mkdir(staging, { recursive: true })
  const entryName = process.platform === "win32" ? "breadboard-engine.exe" : "breadboard-engine"
  const entryPath = path.join(staging, entryName)
  await fs.writeFile(entryPath, "#!/usr/bin/env bash\necho breadboard-engine-smoke\n", "utf8")
  if (process.platform !== "win32") {
    await fs.chmod(entryPath, 0o755)
  }

  const archiveName = `breadboard-engine-${process.platform}-${process.arch}.tar.gz`
  const archivePath = path.join(tmp, archiveName)
  run("tar", ["-czf", archivePath, "-C", staging, entryName])
  const digest = await sha256File(archivePath)

  const version = `smoke-http-${Date.now()}`
  const manifest = {
    version,
    created_at: new Date().toISOString(),
    min_cli_version: "0.1.0",
    protocol_version: "1.0",
    assets: [
      {
        platform: process.platform,
        arch: process.arch,
        url: archiveName,
        sha256: digest,
        size_bytes: (await fs.stat(archivePath)).size,
        entry: entryName,
      },
    ],
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1")
    if (url.pathname === "/manifest.json") {
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify(manifest))
      return
    }
    if (url.pathname === `/${archiveName}`) {
      const body = await fs.readFile(archivePath)
      res.writeHead(200, { "content-type": "application/gzip" })
      res.end(body)
      return
    }
    res.writeHead(404, { "content-type": "text/plain" })
    res.end("not found")
  })

  const port = await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address()
      if (!addr || typeof addr === "string") {
        reject(new Error("Unable to bind http server"))
        return
      }
      resolve(addr.port)
    })
  })

  const manifestUrl = `http://127.0.0.1:${port}/manifest.json`
  try {
    const bundlesModule = await import(pathToFileURL(path.join(tuiDir, "dist", "engine", "engineBundles.js")).toString())
    const downloadBundle = bundlesModule.downloadBundle
    const getEngineCacheRoot = bundlesModule.getEngineCacheRoot
    const resolveCachedBundle = bundlesModule.resolveCachedBundle
    if (typeof downloadBundle !== "function") {
      throw new Error("Unable to import downloadBundle from TUI dist")
    }

    console.log(`[http-bundle-smoke] downloading bundle from ${manifestUrl}`)
    const info = await downloadBundle(manifestUrl, { version, cliVersion: "0.2.0", protocolVersion: "1.0" })
    if (!info?.root || !info?.entry) {
      throw new Error("downloadBundle returned invalid info")
    }
    const resolved = await resolveCachedBundle(version)
    if (!resolved) {
      throw new Error("resolveCachedBundle did not return cached info")
    }
    console.log(`[http-bundle-smoke] ok: ${resolved.entry}`)

    // Cleanup the cached dummy bundle so repeated runs do not accumulate.
    const cacheRoot = typeof getEngineCacheRoot === "function" ? getEngineCacheRoot() : null
    if (cacheRoot) {
      await rimraf(path.join(cacheRoot, version))
    }
  } finally {
    await new Promise((resolve) => server.close(resolve))
    await rimraf(tmp)
  }
}

main().catch((error) => {
  console.error(`[http-bundle-smoke] failed: ${error.message}`)
  process.exit(1)
})
