import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const ROOT_DIR = path.resolve(path.dirname(__filename), "..")
const P16_DESIGN_DIR = path.resolve(
  ROOT_DIR,
  "..",
  "docs_tmp",
  "cli_phase_6",
  "CODESIGN_p16",
  "implementation_validation_p16_final_design_complete",
  "artifacts",
  "design",
)

const REQUIRED_CONTACT_SHEETS = [
  "landing_contact_sheet.png",
  "transcript_hierarchy_contact_sheet.png",
  "tool_card_contact_sheet.png",
  "diff_approval_contact_sheet.png",
  "taskboard_contact_sheet.png",
  "modal_picker_contact_sheet.png",
  "recovery_error_contact_sheet.png",
] as const

const exists = async (filePath: string): Promise<boolean> => {
  try {
    const stat = await fs.stat(filePath)
    return stat.isFile() && stat.size > 0
  } catch {
    return false
  }
}

const main = async (): Promise<void> => {
  const results = await Promise.all(
    REQUIRED_CONTACT_SHEETS.map(async (name) => ({
      name,
      path: path.join(P16_DESIGN_DIR, name),
      exists: await exists(path.join(P16_DESIGN_DIR, name)),
    })),
  )
  const missing = results.filter((result) => !result.exists)
  const report = {
    checkedAt: new Date().toISOString(),
    designDir: P16_DESIGN_DIR,
    verdict: missing.length === 0 ? "pass" : "fail",
    required: results,
    missing: missing.map((result) => result.name),
  }
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  if (missing.length > 0) {
    process.stderr.write(
      `[p16-design-contact-sheets-incomplete] Missing required Phase B contact sheets: ${missing
        .map((result) => result.name)
        .join(", ")}\n`,
    )
    process.exitCode = 1
  }
}

await main()
