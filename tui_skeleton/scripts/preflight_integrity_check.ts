import { mkdirSync } from "node:fs"
import { existsSync } from "node:fs"
import path from "node:path"
import { execFileSync } from "node:child_process"
import { assertRuntimeIntegrity, resolveRepoRoot } from "../src/config/runtimePaths.js"

const args = new Set(process.argv.slice(2))
const snapshot = args.has("--snapshot")

const report = assertRuntimeIntegrity({ includeSource: true })

console.log(JSON.stringify({
  projectRoot: report.projectRoot,
  repoRoot: report.repoRoot,
  engineRoot: report.engineRoot,
  envFilesLoaded: report.envFilesLoaded,
  checkedFiles: report.checkedFiles,
}, null, 2))

if (snapshot) {
  const repoRoot = resolveRepoRoot()
  const outputDir = path.join(repoRoot, "docs_tmp", "recovery_backups")
  mkdirSync(outputDir, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, "-")
  const archivePath = path.join(outputDir, `breadboard_preflight_snapshot_${stamp}.tar.gz`)
  const entries = ["tui_skeleton", "agentic_coder_prototype", "agent_configs", "config"].filter((entry) =>
    existsSync(path.join(repoRoot, entry)),
  )
  execFileSync("tar", ["-czf", archivePath, ...entries], { cwd: repoRoot, stdio: "inherit" })
  console.log(`snapshot=${archivePath}`)
}
