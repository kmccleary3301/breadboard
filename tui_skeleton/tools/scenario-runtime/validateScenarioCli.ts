import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { validateScenario } from "./schema"

const args = process.argv.slice(2)
if (args.length === 0) {
  console.error("Usage: tsx tools/scenario-runtime/validateScenarioCli.ts <scenario.json> [...]")
  process.exit(2)
}

let failed = false
for (const arg of args) {
  const target = path.isAbsolute(arg) ? arg : path.join(process.cwd(), arg)
  const raw = await fs.readFile(target, "utf8")
  const parsed = JSON.parse(raw) as unknown
  const result = validateScenario(parsed)
  if (!result.ok) {
    failed = true
    console.error(`[scenario:validate] FAIL ${arg}`)
    for (const error of result.errors) console.error(`  - ${error}`)
  } else {
    console.log(`[scenario:validate] PASS ${arg}${result.warnings.length ? ` (${result.warnings.length} warning${result.warnings.length === 1 ? "" : "s"})` : ""}`)
    for (const warning of result.warnings) console.log(`  warning: ${warning}`)
  }
}
if (failed) process.exit(1)
