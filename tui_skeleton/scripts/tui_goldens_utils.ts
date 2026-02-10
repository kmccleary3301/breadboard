import { spawnSync } from "node:child_process"
import { promises as fs } from "node:fs"
import path from "node:path"

export type RunResult = {
  ok: boolean
  status: number | null
}

export const runNodeWithTsx = (args: string[]): RunResult => {
  const result = spawnSync(process.execPath, ["--import", "tsx", ...args], {
    stdio: "inherit",
    env: process.env,
  })
  return { ok: result.status === 0, status: result.status }
}

export const findLatestRunDir = async (root: string): Promise<string> => {
  const entries = await fs.readdir(root, { withFileTypes: true })
  const runDirs: { name: string; mtimeMs: number }[] = []

  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    if (!entry.name.startsWith("run-")) continue
    const fullPath = path.join(root, entry.name)
    const stat = await fs.stat(fullPath)
    runDirs.push({ name: entry.name, mtimeMs: stat.mtimeMs })
  }

  runDirs.sort((a, b) => b.mtimeMs - a.mtimeMs)
  const latest = runDirs[0]
  if (!latest) {
    throw new Error(`No run-* directories found under: ${root}`)
  }

  return path.join(root, latest.name)
}

