import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { evaluateInvariants, type InvariantRequest } from "./invariants/invariantRegistry"

const args = process.argv.slice(2)
const artifactDir = args[0]
const scenarioId = args[1] ?? "unknown"
const lane = args[2] ?? "pty"
const invariantsPath = args[3]
if (!artifactDir) {
  console.error("Usage: tsx tools/scenario-runtime/runInvariantsCli.ts <artifact-dir> [scenario-id] [lane] [invariants.json]")
  process.exit(2)
}
const requests: InvariantRequest[] = invariantsPath
  ? JSON.parse(await fs.readFile(path.isAbsolute(invariantsPath) ? invariantsPath : path.join(process.cwd(), invariantsPath), "utf8"))
  : []
const report = await evaluateInvariants(artifactDir, scenarioId, lane, requests)
await fs.writeFile(path.join(artifactDir, "invariant_report.json"), JSON.stringify(report, null, 2), "utf8")
console.log(`[scenario:invariants] ${report.ok ? "PASS" : "FAIL"} ${scenarioId} ${lane}`)
if (!report.ok) process.exit(1)
