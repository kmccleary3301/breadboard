import { promises as fs } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(here, "..")
const distMain = path.join(projectRoot, "dist", "main.js")

const resolveBinDir = (): string => {
  const override = process.env.BREADBOARD_BIN_DIR?.trim()
  if (override) {
    return path.isAbsolute(override) ? override : path.resolve(projectRoot, override)
  }
  return path.join(homedir(), ".local", "bin")
}

const writeWrapper = async (targetPath: string) => {
  const wrapper = `#!/usr/bin/env bash
set -euo pipefail
node ${JSON.stringify(distMain)} \"$@\"
`
  await fs.mkdir(path.dirname(targetPath), { recursive: true })
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
  await ensureDistMainExists()
  const binDir = resolveBinDir()
  await writeWrapper(path.join(binDir, "breadboard"))
  if (process.env.BREADBOARD_INSTALL_QUIET !== "1") {
    console.log(`[breadboard] Installed wrappers into ${binDir}`)
  }
}

void main().catch((error) => {
  console.error(`[breadboard] local bin install failed: ${(error as Error).message}`)
  process.exitCode = 1
})
