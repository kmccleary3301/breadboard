import { promises as fs } from "node:fs"
import process from "node:process"

const target = process.argv[2]
if (!target) throw new Error("Usage: tsx scripts/p14_h2_command_surface_gate.ts <pty_snapshots.txt>")

const raw = await fs.readFile(target, "utf8")
const section = (label: string): string => raw.match(new RegExp(`# ${label}\\n([\\s\\S]*?)(?:\\n# |$)`))?.[1] ?? ""
const root = section("root-popup-normal")
const filtered = section("filter-feature-gated-diff")
const diffResult = section("feature-gated-diff-result")
const debugResult = section("hidden-debug-dispatch")

if (!root.includes("/resume") || !root.includes("/transcript") || !root.includes("/models")) {
  throw new Error("Root slash popup did not render expected registry-backed default rows")
}
if (root.includes("/debug-config")) throw new Error("Hidden debug command leaked into root popup")
if (!filtered.includes("/diff") || !filtered.includes("diff viewer")) {
  throw new Error("Filtered slash popup did not render feature-gated /diff row")
}
if (!diffResult.includes("/diff") || !diffResult.includes("Diff viewer commands are deferred to H5")) {
  throw new Error("Exact /diff did not produce the feature-gated command result")
}
if (!debugResult.includes("/debug-config") || !debugResult.includes("engine mode")) {
  throw new Error("Hidden /debug-config did not dispatch to local debug command result")
}
if (debugResult.includes("TASK COMPLETE")) throw new Error("Command surface gate appears to have submitted to the model")

console.log(JSON.stringify({ verdict: "pass", snapshots: target }, null, 2))
