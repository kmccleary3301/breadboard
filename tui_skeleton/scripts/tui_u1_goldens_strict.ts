import path from "node:path"
import { findLatestRunDir, runNodeWithTsx } from "./tui_goldens_utils.js"

const main = async (): Promise<number> => {
  const manifest = path.resolve("ui_baselines", "u1", "manifests", "u1.yaml")
  const runsRoot = path.resolve("ui_baselines", "u1", "_runs")
  const blessedRoot = path.resolve("ui_baselines", "u1", "scenarios")

  console.log("[tui_u1] rendering...")
  const render = runNodeWithTsx([
    "scripts/run_tui_goldens.ts",
    "--manifest",
    manifest,
    "--out",
    runsRoot,
    "--config",
    path.resolve("..", "agent_configs", "codex_cli_gpt51mini_e4_live.yaml"),
  ])
  if (!render.ok) return render.status ?? 1

  const latestRun = await findLatestRunDir(runsRoot)
  console.log(`[tui_u1] latest run: ${latestRun}`)

  console.log("[tui_u1] compare (strict)...")
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

  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error("[tui_u1_goldens_strict] failed:", error)
    process.exit(1)
  })

