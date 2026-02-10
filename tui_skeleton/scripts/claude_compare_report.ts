import path from "node:path"
import { findLatestRunDir, runNodeWithTsx } from "./tui_goldens_utils.js"

const main = async (): Promise<number> => {
  const manifest = path.resolve("..", "misc", "tui_goldens", "manifests", "claude_compare_manifest.yaml")
  const outRoot = path.resolve("..", "misc", "tui_goldens", "claude_compare")
  const refsRoot = path.resolve("..", "misc", "tui_goldens", "claude_refs")

  console.log("[claude_compare] rendering candidate captures...")
  const render = runNodeWithTsx(["scripts/run_tui_goldens.ts", "--manifest", manifest, "--out", outRoot])
  if (!render.ok) {
    console.error(`[claude_compare] render failed (status=${render.status}); report-only, continuing`)
    return 0
  }

  const latestRun = await findLatestRunDir(outRoot)
  console.log(`[claude_compare] latest run: ${latestRun}`)

  console.log("[claude_compare] compare (report-only)...")
  const compare = runNodeWithTsx([
    "scripts/compare_claude_alignment.ts",
    "--refs-root",
    refsRoot,
    "--candidate-root",
    latestRun,
    "--strict",
    "--summary",
  ])
  if (!compare.ok) {
    console.error(`[claude_compare] compare failed (status=${compare.status}); report-only`)
  }

  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error("[claude_compare_report] failed:", error)
    process.exit(0)
  })
