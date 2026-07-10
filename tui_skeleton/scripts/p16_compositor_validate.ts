import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { p16CompositorJsonSchema, validateP16CompositorScene } from "../tools/p16-compositor/schema.js"

const ROOT_DIR = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..")
const REPO_DIR = path.resolve(ROOT_DIR, "..")
const P16_DIR = path.join(REPO_DIR, "docs_tmp", "cli_phase_6", "CODESIGN_p16", "implementation_validation_p16_final_design_complete")
const COMPOSITOR_ARTIFACT_DIR = path.join(P16_DIR, "artifacts", "compositor")
const DEFAULT_SCENE_DIR = path.join(ROOT_DIR, "scenarios", "p16", "compositor")

const collectJson = async (target: string): Promise<string[]> => {
  const stat = await fs.stat(target)
  if (stat.isFile()) return [target]
  const entries = await fs.readdir(target, { withFileTypes: true })
  const files: string[] = []
  for (const entry of entries) {
    const full = path.join(target, entry.name)
    if (entry.isDirectory()) files.push(...await collectJson(full))
    else if (entry.isFile() && entry.name.endsWith(".json")) files.push(full)
  }
  return files.sort()
}

const main = async () => {
  const target = process.argv[2] ? path.resolve(process.argv[2]) : DEFAULT_SCENE_DIR
  await fs.mkdir(COMPOSITOR_ARTIFACT_DIR, { recursive: true })
  await fs.writeFile(path.join(COMPOSITOR_ARTIFACT_DIR, "scene_schema.json"), `${JSON.stringify(p16CompositorJsonSchema(), null, 2)}\n`, "utf8")
  const files = await collectJson(target)
  const results = []
  for (const file of files) {
    const raw = JSON.parse(await fs.readFile(file, "utf8"))
    const result = validateP16CompositorScene(raw)
    results.push({ file, ok: result.ok, errors: result.errors })
  }
  const failures = results.filter((result) => !result.ok)
  const report = { checkedAt: new Date().toISOString(), target, schemaPath: path.join(COMPOSITOR_ARTIFACT_DIR, "scene_schema.json"), files: results, verdict: failures.length === 0 ? "pass" : "fail" }
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (failures.length > 0) process.exitCode = 1
}

await main()
