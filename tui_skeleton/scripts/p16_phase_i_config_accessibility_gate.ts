import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"

const artifactDir = process.argv[2]
if (!artifactDir) {
  console.error("Usage: tsx scripts/p16_phase_i_config_accessibility_gate.ts <artifact-dir>")
  process.exit(2)
}

const root = path.resolve(artifactDir)
await fs.mkdir(root, { recursive: true })

const failures: string[] = []
const notes: string[] = []

const readJson = async <T>(file: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(file, "utf8")) as T
  } catch {
    return null
  }
}

const requireBatchPass = async (name: string): Promise<void> => {
  const manifestPath = path.join(root, name, "batch_manifest.json")
  const manifest = await readJson<{ ok?: boolean; redCount?: number; results?: unknown[] }>(manifestPath)
  if (!manifest) {
    failures.push(`${name}: missing batch_manifest.json`)
    return
  }
  if (manifest.ok !== true) failures.push(`${name}: batch ok is not true`)
  if (Number(manifest.redCount ?? 0) !== 0) failures.push(`${name}: redCount=${String(manifest.redCount)}`)
  if (!Array.isArray(manifest.results) || manifest.results.length === 0) failures.push(`${name}: empty results`)
  notes.push(`${name}: ok=${String(manifest.ok)} redCount=${String(manifest.redCount)} results=${String(manifest.results?.length ?? 0)}`)
}

await requireBatchPass("performance_long_session")
await requireBatchPass("accessibility_keyboard")
await requireBatchPass("config_conformance_final")

process.env.NO_COLOR = "1"
process.env.BREADBOARD_TUI_ASCII_ONLY = "1"
process.env.BREADBOARD_ASCII = "1"

const [{ resolveTuiConfig }, theme] = await Promise.all([
  import("../src/tui_config/load.ts"),
  import("../src/repl/components/replView/theme.ts"),
])

const resolvedConfig = await resolveTuiConfig({
  workspace: ".",
  cliStrict: true,
  colorAllowed: true,
})

if (resolvedConfig.display.asciiOnly !== true) failures.push("ascii-only config did not resolve true")
if (resolvedConfig.display.colorMode !== "none") failures.push(`NO_COLOR did not resolve colorMode=none: ${resolvedConfig.display.colorMode}`)
if (theme.ASCII_ONLY !== true) failures.push("theme ASCII_ONLY did not resolve true")

const packageJson = await readJson<{ scripts?: Record<string, string> }>(path.join(process.cwd(), "package.json"))
for (const scriptName of [
  "p16:phase-i:performance-long-session",
  "p16:phase-i:accessibility-keyboard",
  "p16:phase-i:config-conformance",
  "p16:phase-i:all",
]) {
  if (!packageJson?.scripts?.[scriptName]) failures.push(`missing package script ${scriptName}`)
}

const configFiles = [
  "../agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
  "../agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
]
const configPresence: Record<string, boolean> = {}
for (const configPath of configFiles) {
  const resolved = path.resolve(process.cwd(), configPath)
  try {
    await fs.access(resolved)
    configPresence[configPath] = true
  } catch {
    configPresence[configPath] = false
    failures.push(`missing expected Codex E4 config ${configPath}`)
  }
}

const promptLeakPatterns = [
  /You are Codex, based on GPT-5/i,
  /## Editing constraints/i,
  /## Presenting your work/i,
  /NEVER revert existing changes/i,
  /Default: be very concise/i,
]
const scanBatchText = async (name: string): Promise<void> => {
  const dir = path.join(root, name)
  let entries: string[] = []
  try {
    entries = await fs.readdir(dir, { recursive: true }) as string[]
  } catch {
    return
  }
  const textFiles = entries.filter((entry) => /\.(txt|md|json|csv|ndjson|log)$/.test(entry)).slice(0, 500)
  for (const entry of textFiles) {
    const file = path.join(dir, entry)
    let text = ""
    try {
      text = await fs.readFile(file, "utf8")
    } catch {
      continue
    }
    for (const pattern of promptLeakPatterns) {
      if (pattern.test(text)) {
        failures.push(`${name}: prompt/config leak pattern ${pattern.source} in ${entry}`)
      }
    }
  }
}
await scanBatchText("performance_long_session")
await scanBatchText("accessibility_keyboard")
await scanBatchText("config_conformance_final")

const report = {
  schemaVersion: "bb.p16.phase_i.config_accessibility_gate.v1",
  artifactDir: root,
  verdict: failures.length === 0 ? "pass" : "fail",
  failures,
  notes,
  ascii: {
    configAsciiOnly: resolvedConfig.display.asciiOnly,
    colorMode: resolvedConfig.display.colorMode,
    themeAsciiOnly: theme.ASCII_ONLY,
  },
  configPresence,
}

await fs.writeFile(path.join(root, "p16_phase_i_config_accessibility_gate.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
await fs.writeFile(
  path.join(root, "phase_i_gate_report.md"),
  [
    "# P16 Phase I Config/Accessibility Gate",
    "",
    `- verdict: ${report.verdict}`,
    `- artifactDir: ${root}`,
    `- asciiOnly: ${String(report.ascii.configAsciiOnly)}`,
    `- colorMode: ${report.ascii.colorMode}`,
    `- themeAsciiOnly: ${String(report.ascii.themeAsciiOnly)}`,
    "",
    "## Batch Notes",
    ...notes.map((note) => `- ${note}`),
    "",
    "## Failures",
    ...(failures.length === 0 ? ["- none"] : failures.map((failure) => `- ${failure}`)),
    "",
  ].join("\n"),
  "utf8",
)

if (failures.length > 0) {
  console.error(JSON.stringify(report, null, 2))
  process.exit(1)
}
console.log(JSON.stringify(report, null, 2))
