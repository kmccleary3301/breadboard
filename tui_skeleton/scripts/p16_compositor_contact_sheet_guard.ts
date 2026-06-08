import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const ROOT_DIR = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..")
const REPO_DIR = path.resolve(ROOT_DIR, "..")
const P16_DIR = path.join(REPO_DIR, "docs_tmp", "cli_phase_6", "CODESIGN_p16", "implementation_validation_p16_final_design_complete")
const COMPOSITOR_ARTIFACT_DIR = path.join(P16_DIR, "artifacts", "compositor")

const main = async () => {
  const manifestPath = path.join(COMPOSITOR_ARTIFACT_DIR, "latest_manifest.json")
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"))
  const missing: string[] = []
  for (const scene of manifest.scenes ?? []) {
    const png = path.join(scene.outDir, "screenshots", "contact_sheet.png")
    const report = path.join(scene.outDir, "invariant_report.json")
    for (const file of [png, report]) {
      try {
        const stat = await fs.stat(file)
        if (stat.size <= 0) missing.push(file)
      } catch {
        missing.push(file)
      }
    }
  }
  const verdict = manifest.verdict === "pass" && missing.length === 0 ? "pass" : "fail"
  const report = { checkedAt: new Date().toISOString(), manifestPath, verdict, missing }
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (verdict !== "pass") process.exitCode = 1
}

await main()
