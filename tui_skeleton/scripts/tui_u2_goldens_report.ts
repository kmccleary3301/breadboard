import path from "node:path"
import { findLatestRunDir, runNodeWithTsx } from "./tui_goldens_utils.js"

const main = async (): Promise<number> => {
  delete process.env.NO_COLOR
  process.env.FORCE_COLOR = "1"
  process.env.BREADBOARD_ASCII = "0"

  const manifest = path.resolve("ui_baselines", "u2", "manifests", "u2.yaml")
  const runsRoot = path.resolve("ui_baselines", "u2", "_runs")
  const blessedRoot = path.resolve("ui_baselines", "u2", "scenarios")

  console.log("[tui_u2] rendering...")
  const render = runNodeWithTsx([
    "scripts/run_tui_goldens.ts",
    "--manifest",
    manifest,
    "--out",
    runsRoot,
    "--config",
    path.resolve("..", "agent_configs", "codex_cli_gpt51mini_e4_live.yaml"),
  ])
  if (!render.ok) {
    console.error(`[tui_u2] render failed (status=${render.status}); report-only, continuing`)
    return 0
  }

  const latestRun = await findLatestRunDir(runsRoot)
  console.log(`[tui_u2] latest run: ${latestRun}`)

  console.log("[tui_u2] compare (report-only)...")
  const compare = runNodeWithTsx([
    "scripts/compare_tui_goldens.ts",
    "--manifest",
    manifest,
    "--candidate",
    latestRun,
    "--blessed-root",
    blessedRoot,
    "--summary",
  ])
  if (!compare.ok) {
    console.error(`[tui_u2] compare failed (status=${compare.status}); report-only`)
  }

  return 0
}

main()
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error("[tui_u2_goldens_report] failed:", error)
    process.exit(0)
  })
