import fs from "node:fs"
import path from "node:path"

const normalize = (value: string): string => value.replace(/\r\n/g, "\n").trimEnd()

export const compareSnapshotToFile = (actual: string, relativePath: string): void => {
  const targetPath = path.resolve(relativePath)
  const expected = normalize(fs.readFileSync(targetPath, "utf8"))
  if (normalize(actual) !== expected) {
    throw new Error(`Snapshot mismatch for ${relativePath}`)
  }
}
