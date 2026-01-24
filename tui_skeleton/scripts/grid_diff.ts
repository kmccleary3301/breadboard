#!/usr/bin/env tsx
import process from "node:process"
import { computeFileGridDiff } from "../tools/tty/gridDiff.js"

const main = async () => {
  const [fileA, fileB] = process.argv.slice(2)
  if (!fileA || !fileB) {
    console.error("Usage: tsx scripts/grid_diff.ts <gridA> <gridB>")
    process.exit(1)
    return
  }
  const result = await computeFileGridDiff(fileA, fileB)
  process.stdout.write(result.report)
  if (!result.areEqual) {
    process.exitCode = 2
  }
}

main().catch((error) => {
  console.error("grid_diff failed:", error)
  process.exitCode = 1
})
