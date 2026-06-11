import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs"
import path from "node:path"

type Severity = "blocker" | "allowlisted"

interface Finding {
  readonly severity: Severity
  readonly rule: string
  readonly file: string
  readonly line: number
  readonly text: string
  readonly reason?: string
}

const root = process.cwd()
const outputPath =
  process.argv.find((arg) => arg.startsWith("--out="))?.slice("--out=".length) ??
  path.join(root, "scripts", "_tmp_resize_spine_bypass_scan.md")
const allowlistBudgetRaw = process.env.BREADBOARD_RESIZE_SPINE_MAX_ALLOWLISTED ?? "114"
const allowlistBudget = Number(allowlistBudgetRaw)

const scanRoots = [
  "src/commands/repl",
  "src/repl",
  "src/engine",
]

const sourceExtensions = new Set([".ts", ".tsx"])

const walk = (dir: string): string[] => {
  if (!existsSync(dir)) return []
  const out: string[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === "dist" || entry.name === "__tests__") continue
    const next = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(next))
    if (entry.isFile() && sourceExtensions.has(path.extname(entry.name))) out.push(next)
  }
  return out
}

const isDebugConsole = (windowText: string): boolean => /DEBUG_[A-Z_]+/.test(windowText)

const allowlist = (rule: string, file: string, lineText: string, windowText: string): string | null => {
  const normalized = file.replaceAll(path.sep, "/")
  if (rule === "direct-terminal-io") {
    if (normalized === "src/commands/repl/command.tsx") return "CLI/Ink stdout adapter boundary"
    if (normalized.includes("terminalControl") || normalized.includes("scrollbackSafeInkStdout")) {
      return "terminal control adapter boundary"
    }
  }
  if (rule === "console-runtime") {
    if (normalized === "src/commands/repl/command.tsx") return "CLI command boundary"
    if (normalized === "src/engine/engineSupervisor.ts") return "engine lifecycle process log boundary"
    if (isDebugConsole(windowText)) return "debug-only console branch"
    if (/debug|DEBUG|BREADBOARD_DEBUG/i.test(windowText)) return "debug-only console branch"
  }
  if (rule === "raw-json-visible") {
    if (normalized === "src/commands/repl/controllerStateMethods.ts" && lineText.includes("[status] completion")) {
      return "known legacy status summary exception"
    }
    if (normalized === "src/commands/repl/controllerEventMethods.ts" && lineText.includes("[usage]")) {
      return "known legacy usage summary exception"
    }
    if (normalized === "src/commands/repl/controllerEventMethods.ts" && lineText.includes("[reward]")) {
      return "known legacy reward summary exception"
    }
    if (normalized === "src/commands/repl/controllerEventMethods.ts" && /lineParts\.length|JSON\.stringify\(payload\)/.test(windowText)) {
      return "known legacy structured-status fallback exception"
    }
    if (normalized === "src/commands/repl/controllerUserMethods.ts" && lineText.includes("[command]")) {
      return "known legacy command echo exception"
    }
  }
  if (rule === "raw-event-visible") {
    if (normalized === "src/commands/repl/command.tsx") return "CLI script streaming adapter boundary"
    if (normalized === "src/commands/repl/controller.ts" && lineText.includes("payloadKey")) {
      return "internal event dedupe key"
    }
    if (normalized === "src/commands/repl/controllerUtils.ts") return "shared payload formatting utility"
    if (normalized === "src/commands/repl/controllerStateMethods.ts") return "raw event inspector buffer"
    if (normalized === "src/commands/repl/controllerEventMethods.ts" && isDebugConsole(windowText)) return "debug-only event logging"
    if (normalized === "src/commands/repl/controllerEventMethods.ts") return "known legacy event projection fallback"
    if (normalized === "src/commands/repl/controllerUserMethods.ts") return "known legacy command payload echo"
    if (normalized === "src/repl/components/replView/controller/transcriptMemoryBounds.ts") {
      return "internal transcript memory sizing"
    }
    if (normalized === "src/repl/transcript/normalizeSessionEvent.ts") return "transcript export normalization fallback"
  }
  if (rule === "raw-ansi-control") {
    if (normalized === "src/commands/repl/renderText.ts") return "semantic ANSI renderer boundary"
    if (normalized === "src/repl/inkScrollbackStdout.ts") return "terminal stdout adapter boundary"
    if (normalized.includes("src/repl/components/LineEditor")) return "key-sequence parser boundary"
    if (normalized.includes("src/repl/components/replView")) return "live view terminal control boundary"
    if (normalized.includes("src/repl/components/replView/keybindings")) return "key-sequence parser boundary"
  }
  return null
}

const rules: ReadonlyArray<{ readonly id: string; readonly pattern: RegExp }> = [
  { id: "direct-terminal-io", pattern: /\bprocess\.(?:stdout|stderr)\.write\b/ },
  { id: "console-runtime", pattern: /\bconsole\.(?:log|error|warn)\s*\(/ },
  { id: "raw-json-visible", pattern: /\b(?:addTool|addConversation)\s*\([^;\n]*JSON\.stringify|`\[[^\]]+\][^`]*\$\{JSON\.stringify\(/ },
  { id: "raw-event-visible", pattern: /JSON\.stringify\((?:event\.payload|snapshot|payload)\)/ },
  { id: "raw-ansi-control", pattern: /\\x1b\[|\\u001b\[|\u001b\[/ },
]

const findings: Finding[] = []

for (const scanRoot of scanRoots) {
  for (const filePath of walk(path.join(root, scanRoot))) {
    const relative = path.relative(root, filePath)
    const lines = readFileSync(filePath, "utf8").split(/\r?\n/)
    lines.forEach((lineText, index) => {
      for (const rule of rules) {
        if (!rule.pattern.test(lineText)) continue
        const windowText = lines.slice(Math.max(0, index - 6), Math.min(lines.length, index + 7)).join("\n")
        const reason = allowlist(rule.id, relative, lineText, windowText)
        findings.push({
          severity: reason ? "allowlisted" : "blocker",
          rule: rule.id,
          file: relative,
          line: index + 1,
          text: lineText.trim(),
          reason: reason ?? undefined,
        })
      }
    })
  }
}

const blockers = findings.filter((finding) => finding.severity === "blocker")
const allowlisted = findings.filter((finding) => finding.severity === "allowlisted")
const budgetExceeded = Number.isFinite(allowlistBudget) && allowlisted.length > allowlistBudget
const renderFinding = (finding: Finding): string =>
  `- ${finding.severity.toUpperCase()} ${finding.rule} ${finding.file}:${finding.line}` +
  `${finding.reason ? ` (${finding.reason})` : ""}\n  \`${finding.text.replaceAll("`", "'")}\``

const report = [
  "# Resize Spine Bypass Scan",
  "",
  `root: ${root}`,
  `blockers: ${blockers.length}`,
  `allowlisted: ${allowlisted.length}`,
  `allowlistBudget: ${Number.isFinite(allowlistBudget) ? allowlistBudget : "disabled"}`,
  `budgetExceeded: ${budgetExceeded}`,
  "",
  "## Blockers",
  "",
  blockers.length > 0 ? blockers.map(renderFinding).join("\n") : "None.",
  "",
  "## Allowlisted Existing Boundaries",
  "",
  allowlisted.length > 0 ? allowlisted.map(renderFinding).join("\n") : "None.",
  "",
].join("\n")

writeFileSync(outputPath, report, "utf8")
if (blockers.length > 0 || budgetExceeded) {
  console.error(report)
  if (budgetExceeded) {
    console.error(
      `[resize-spine] allowlist grew from budget ${allowlistBudget} to ${allowlisted.length}; reduce exceptions or raise the documented budget intentionally.`,
    )
  }
  process.exit(1)
}
console.log(report)
