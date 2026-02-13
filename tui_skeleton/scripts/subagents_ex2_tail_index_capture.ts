import { spawnSync } from "node:child_process"
import { mkdirSync } from "node:fs"
import path from "node:path"

const main = (): void => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const outPath = path.join(outputDir, "ex2_focus_tail_index_cache_capture_20260213.json")
  const scriptPath = path.resolve("..", "scripts", "subagents_ex2_tail_index_capture.py")
  const result = spawnSync("python3", [scriptPath, "--out", outPath], {
    cwd: process.cwd(),
    stdio: "pipe",
    encoding: "utf8",
  })
  const stdout = String(result.stdout ?? "")
  const stderr = String(result.stderr ?? "")
  const combined = `${stdout}${stderr}`.trim()
  if (combined.length > 0) console.log(combined)
  const exitCode = typeof result.status === "number" ? result.status : 1
  if (exitCode !== 0) process.exit(exitCode)
  console.log(`wrote ${path.relative(process.cwd(), outPath)}`)
}

main()

