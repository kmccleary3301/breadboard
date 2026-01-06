import { createHash } from "node:crypto"
import { promises as fs } from "node:fs"
import path from "node:path"

const hashFile = async (filePath: string): Promise<{ sha256: string; bytes: number } | null> => {
  try {
    const data = await fs.readFile(filePath)
    const sha256 = createHash("sha256").update(data).digest("hex")
    return { sha256, bytes: data.length }
  } catch {
    return null
  }
}

export const buildManifestHashes = async (
  batchDir: string,
  caseSummaries: Array<Record<string, unknown>>,
): Promise<Record<string, Record<string, { sha256: string; bytes: number }>> | null> => {
  if (!Array.isArray(caseSummaries)) return null
  const result: Record<string, Record<string, { sha256: string; bytes: number }>> = {}
  for (const summary of caseSummaries) {
    const caseId = typeof summary.id === "string" ? summary.id : null
    if (!caseId) continue
    const outputs = (summary as Record<string, unknown>).outputs
    if (!outputs || typeof outputs !== "object" || Array.isArray(outputs)) continue
    const entries = outputs as Record<string, unknown>
    const hashes: Record<string, { sha256: string; bytes: number }> = {}
    for (const [key, value] of Object.entries(entries)) {
      if (typeof value !== "string") continue
      const resolved = path.isAbsolute(value) ? value : path.join(batchDir, value)
      const digest = await hashFile(resolved)
      if (!digest) continue
      hashes[key] = digest
    }
    if (Object.keys(hashes).length > 0) {
      result[caseId] = hashes
    }
  }
  return Object.keys(result).length > 0 ? result : null
}
