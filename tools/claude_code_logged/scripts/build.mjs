#!/usr/bin/env node

import fs from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"
import process from "node:process"

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, "..")
const distDir = path.join(rootDir, "dist")
const srcDir = path.join(rootDir, "src")

const DEFAULT_VENDOR = path.resolve(
  rootDir,
  "../../industry_coder_refs/claude_code_cli/node_modules/@anthropic-ai/claude-code",
)

function parseArgs() {
  const args = process.argv.slice(2)
  const result = { vendor: DEFAULT_VENDOR }
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    if (arg === "--vendor" && args[i + 1]) {
      result.vendor = path.resolve(args[i + 1])
      i += 1
    }
  }
  return result
}

async function copyVendorTree(vendorPath) {
  await fs.rm(distDir, { recursive: true, force: true })
  await fs.mkdir(distDir, { recursive: true })
  await fs.cp(vendorPath, distDir, { recursive: true })
}

async function injectLogger(versionTag) {
  const cliPath = path.join(distDir, "cli.js")
  let content = await fs.readFile(cliPath, "utf8")
  if (content.includes("installFetchLogger")) {
    return
  }
  const shebang = content.startsWith("#!") ? content.slice(0, content.indexOf("\n") + 1) : ""
  const body = shebang ? content.slice(shebang.length) : content
  const banner = `import { installFetchLogger } from "./fetch_logger.js";\ninstallFetchLogger({ cliVersion: "${versionTag}" });\n`
  await fs.writeFile(cliPath, `${shebang}${banner}${body}`, "utf8")
}

async function rewritePackageJson(versionTag) {
  const pkgPath = path.join(distDir, "package.json")
  const pkg = JSON.parse(await fs.readFile(pkgPath, "utf8"))
  pkg.name = "claude-code-logged"
  pkg.bin = {
    "claude-code-logged": "./claude-code-logged",
  }
  pkg.version = versionTag
  await fs.writeFile(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`, "utf8")
}

async function createLauncher() {
  const launcherPath = path.join(distDir, "claude-code-logged")
  const script = [
    "#!/usr/bin/env bash",
    'set -euo pipefail',
    'DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
    'exec node "$DIR/cli.js" "$@"',
    "",
  ].join("\n")
  await fs.writeFile(launcherPath, script, "utf8")
  await fs.chmod(launcherPath, 0o755)
}

async function main() {
  const { vendor } = parseArgs()
  const vendorPkgPath = path.join(vendor, "package.json")
  const vendorPkg = JSON.parse(await fs.readFile(vendorPkgPath, "utf8"))
  const versionTag = `${vendorPkg.version}-logged`

  console.log(`[claude-code-logged] Using vendor bundle at ${vendor}`)
  await copyVendorTree(vendor)
  await fs.cp(srcDir, distDir, { recursive: true })
  await injectLogger(versionTag)
  await rewritePackageJson(versionTag)
  await createLauncher()

  console.log(`[claude-code-logged] Build complete -> ${distDir}`)
  console.log(`[claude-code-logged] Binary available as 'claude-code-logged' once dist is on PATH`)
}

main().catch((error) => {
  console.error("[claude-code-logged] Build failed:", error)
  process.exitCode = 1
})
