import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { readP16CompositorScene } from "../tools/p16-compositor/schema.js"
import { renderP16CompositorScene } from "../tools/p16-compositor/render.js"

const ROOT_DIR = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..")
const REPO_DIR = path.resolve(ROOT_DIR, "..")
const P16_DIR = path.join(REPO_DIR, "docs_tmp", "cli_phase_6", "CODESIGN_p16", "implementation_validation_p16_final_design_complete")
const COMPOSITOR_ARTIFACT_DIR = path.join(P16_DIR, "artifacts", "compositor")
const DEFAULT_SCENE_DIR = path.join(ROOT_DIR, "scenarios", "p16", "compositor")

const timestamp = () => {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

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
  const runId = `compositor_${timestamp()}`
  const outRoot = path.join(COMPOSITOR_ARTIFACT_DIR, runId)
  await fs.mkdir(outRoot, { recursive: true })
  const files = await collectJson(target)
  const results = []
  for (const file of files) {
    const scene = await readP16CompositorScene(file)
    const sceneOut = path.join(outRoot, scene.id)
    const result = await renderP16CompositorScene(scene, sceneOut, { repoRoot: REPO_DIR, tuiRoot: ROOT_DIR, command: `pnpm run p16:compositor:render ${target}` })
    results.push({ sceneId: scene.id, expectedOutcome: scene.expectedOutcome, knownBadKind: scene.knownBadKind ?? null, verdict: result.verdict, expectedRedObserved: result.expectedRedObserved, outDir: sceneOut })
  }
  const failures = results.filter((result) => result.verdict !== "pass")
  const manifest = { generatedAt: new Date().toISOString(), runId, target, outRoot, verdict: failures.length === 0 ? "pass" : "fail", scenes: results, claimBoundary: "Production ReplView/controller compositor evidence only; not real-host proof." }
  await fs.writeFile(path.join(outRoot, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(COMPOSITOR_ARTIFACT_DIR, "latest_manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`)
  if (failures.length > 0) process.exitCode = 1
}

await main()
