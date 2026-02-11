import path from "node:path"
import { findLatestRunDir, runNodeWithTsx } from "./tui_goldens_utils.js"

const main = async (): Promise<number> => {
  const manifest = path.resolve("..", "misc", "tui_goldens", "manifests", "tui_goldens.yaml")
  const artifactsRoot = path.resolve("..", "misc", "tui_goldens", "artifacts")
  const blessedRoot = path.resolve("..", "misc", "tui_goldens", "scenarios")

  console.log("[tui_goldens] rendering...")
  const render = runNodeWithTsx(["scripts/run_tui_goldens.ts", "--manifest", manifest, "--out", artifactsRoot])
  if (!render.ok) return render.status ?? 1

  const latestRun = await findLatestRunDir(artifactsRoot)
  console.log(`[tui_goldens] latest run: ${latestRun}`)

  console.log("[tui_goldens] compare (strict)...")
  const compare = runNodeWithTsx([
    "scripts/compare_tui_goldens.ts",
    "--manifest",
    manifest,
    "--candidate",
    latestRun,
    "--blessed-root",
    blessedRoot,
    "--strict",
    "--summary",
  ])
  if (!compare.ok) return compare.status ?? 1

  console.log("[tui_goldens] grid compare (strict)...")
  const grid = runNodeWithTsx([
    "scripts/compare_tui_goldens_grid.ts",
    "--manifest",
    manifest,
    "--candidate",
    latestRun,
    "--blessed-root",
    blessedRoot,
    "--strict",
    "--summary",
  ])
  if (!grid.ok) return grid.status ?? 1

  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error("[tui_goldens_strict] failed:", error)
    process.exit(1)
  })

