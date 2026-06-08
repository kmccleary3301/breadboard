import crypto from "node:crypto"
import { promises as fs } from "node:fs"
import path from "node:path"

export const sha256 = (text: string | Buffer): string => crypto.createHash("sha256").update(text).digest("hex")

export const ensureDir = async (dir: string) => {
  await fs.mkdir(dir, { recursive: true })
}

export const writeJson = async (target: string, value: unknown) => {
  await ensureDir(path.dirname(target))
  await fs.writeFile(target, JSON.stringify(value, null, 2), "utf8")
}

export const writeText = async (target: string, value: string) => {
  await ensureDir(path.dirname(target))
  await fs.writeFile(target, value, "utf8")
}

export const copyIfExists = async (source: string, target: string): Promise<boolean> => {
  try {
    await ensureDir(path.dirname(target))
    await fs.copyFile(source, target)
    return true
  } catch {
    return false
  }
}

export const summarizeRun = (options: {
  scenarioId: string
  lane: string
  verdict: "pass" | "fail"
  invariantOk?: boolean
  artifactDir: string
  failedInvariants?: string[]
  warnings?: string[]
}) => `# Scenario Run Summary

- scenario: ${options.scenarioId}
- lane: ${options.lane}
- verdict: ${options.verdict}
- invariantOk: ${String(options.invariantOk ?? false)}
- artifactDir: ${options.artifactDir}
- failedInvariants: ${(options.failedInvariants ?? []).join(", ") || "none"}
- warnings: ${(options.warnings ?? []).join(", ") || "none"}
`
