import { promises as fs } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const shouldSkipInstall = (): boolean => {
  const raw = process.env.BREADBOARD_SKIP_LOCAL_BIN_INSTALL?.trim().toLowerCase()
  return raw === "1" || raw === "true" || raw === "yes"
}

const ensureSourceMainExists = async () => {
  const srcOk = await fs
    .access(srcMain)
    .then(() => true)
    .catch(() => false)
  if (!srcOk) {
    throw new Error(`Missing ${srcMain}.`)
  }
  const tsxLocalPackage = path.join(projectRoot, "node_modules", "tsx", "package.json")
  const tsxOk = await fs
    .access(tsxLocalPackage)
    .then(() => true)
    .catch(() => false)
  if (!tsxOk) {
    throw new Error(`Missing ${tsxLocalPackage}. Run \`npm install\` first.`)
  }
}

const here = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(here, "..")
const repoRoot = path.resolve(projectRoot, "..")
const distMain = path.join(projectRoot, "dist", "main.js")
const srcMain = path.join(projectRoot, "src", "main.ts")
const launcherMode = (process.env.BREADBOARD_LAUNCHER_MODE?.trim().toLowerCase() || "dist") as
  | "dist"
  | "source"

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

const BIN_NAMES = ["breadboard", "bb"] as const

const writeWrapper = async (binDir: string) => {
  await fs.mkdir(binDir, { recursive: true })
  if (process.platform === "win32") {
    const cmd = `@echo off\r\nnode ${JSON.stringify(distMain)} %*\r\n`
    for (const name of BIN_NAMES) {
      await fs.writeFile(path.join(binDir, `${name}.cmd`), cmd, "utf8")
    }
    const ps1 = `#!/usr/bin/env pwsh\nnode ${JSON.stringify(distMain)} @args\n`
    for (const name of BIN_NAMES) {
      await fs.writeFile(path.join(binDir, `${name}.ps1`), ps1, "utf8")
    }
    return
  }

  const wrapper =
    launcherMode === "source"
      ? `#!/usr/bin/env bash
set -euo pipefail
if [ ! -f ${JSON.stringify(path.join(projectRoot, "package.json"))} ] || [ ! -f ${JSON.stringify(path.join(repoRoot, "agentic_coder_prototype", "utils", "safe_delete.py"))} ] || [ ! -f ${JSON.stringify(srcMain)} ]; then
  echo "BreadBoard launcher target is incomplete or corrupted: ${projectRoot}" >&2
  exit 1
fi
cd ${JSON.stringify(projectRoot)}
node --import tsx ${JSON.stringify(srcMain)} \"$@\"
`
      : `#!/usr/bin/env bash
set -euo pipefail
if [ ! -f ${JSON.stringify(path.join(projectRoot, "package.json"))} ] || [ ! -f ${JSON.stringify(path.join(repoRoot, "agentic_coder_prototype", "utils", "safe_delete.py"))} ] || [ ! -f ${JSON.stringify(distMain)} ]; then
  echo "BreadBoard launcher target is incomplete or corrupted: ${projectRoot}" >&2
  exit 1
fi
node ${JSON.stringify(distMain)} \"$@\"
`
  for (const name of BIN_NAMES) {
    const targetPath = path.join(binDir, name)
    await fs.writeFile(targetPath, wrapper, "utf8")
    await fs.chmod(targetPath, 0o755)
  }
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
  if (launcherMode === "source") {
    await ensureSourceMainExists()
  } else {
    await ensureDistMainExists()
  }
  const binDir = resolveBinDir()
  await writeWrapper(binDir)
  if (process.env.BREADBOARD_INSTALL_QUIET !== "1") {
    console.log(`[breadboard] Installed ${launcherMode} wrappers (${BIN_NAMES.join(", ")}) into ${binDir}`)
  }
}

void main().catch((error) => {
  console.error(`[breadboard] local bin install failed: ${(error as Error).message}`)
  process.exitCode = 1
})
