import { promises as fs } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const shouldSkipInstall = (): boolean => {
  const raw = process.env.BREADBOARD_SKIP_LOCAL_BIN_INSTALL?.trim().toLowerCase()
  return raw === "1" || raw === "true" || raw === "yes"
}

const here = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(here, "..")
const distMain = path.join(projectRoot, "dist", "main.js")

const resolveBinDir = (): string => {
  const override = process.env.BREADBOARD_BIN_DIR?.trim()
  if (override) {
    return path.isAbsolute(override) ? override : path.resolve(projectRoot, override)
  }
  if (process.platform === "win32") {
    return path.join(homedir(), ".breadboard", "bin")
  }
  return path.join(homedir(), ".local", "bin")
}

const writeWrapper = async (binDir: string) => {
  await fs.mkdir(binDir, { recursive: true })
  if (process.platform === "win32") {
    const cmd = `@echo off\r\nnode ${JSON.stringify(distMain)} %*\r\n`
    await fs.writeFile(path.join(binDir, "breadboard.cmd"), cmd, "utf8")
    const ps1 = `#!/usr/bin/env pwsh\nnode ${JSON.stringify(distMain)} @args\n`
    await fs.writeFile(path.join(binDir, "breadboard.ps1"), ps1, "utf8")
    return
  }

  const wrapper = `#!/usr/bin/env bash
set -euo pipefail
node ${JSON.stringify(distMain)} \"$@\"
`
  const targetPath = path.join(binDir, "breadboard")
  await fs.writeFile(targetPath, wrapper, "utf8")
  await fs.chmod(targetPath, 0o755)
}

const ensureDistMainExists = async () => {
  const ok = await fs
    .access(distMain)
    .then(() => true)
    .catch(() => false)
  if (!ok) {
    throw new Error(`Missing ${distMain}. Run \`npm run build\` first.`)
  }
}

const main = async () => {
  if (shouldSkipInstall()) {
    return
  }
  await ensureDistMainExists()
  const binDir = resolveBinDir()
  await writeWrapper(binDir)
  if (process.env.BREADBOARD_INSTALL_QUIET !== "1") {
    console.log(`[breadboard] Installed wrappers into ${binDir}`)
  }
}

void main().catch((error) => {
  console.error(`[breadboard] local bin install failed: ${(error as Error).message}`)
  process.exitCode = 1
})
