import { promises as fs } from "node:fs"
import path from "node:path"

interface LayoutAnomaly {
  readonly id: string
  readonly message: string
}

const parseArgs = (): { caseDir: string } => {
  const args = process.argv.slice(2)
  let caseDir: string | undefined
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--case-dir") caseDir = args[++i]
  }
  if (caseDir == null) throw new Error("--case-dir is required")
  return { caseDir }
}

const readText = async (filePath: string): Promise<string> => {
  try {
    return await fs.readFile(filePath, "utf8")
  } catch {
    return ""
  }
}

const readJson = async <T>(filePath: string): Promise<T | null> => {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8")) as T
  } catch {
    return null
  }
}

export const evaluateLiveWrapperEmulatorSmoke = async (caseDir: string): Promise<LayoutAnomaly[]> => {
  const anomalies: LayoutAnomaly[] = []
  const caseInfo = await readJson<{ config?: string; script?: string }>(path.join(caseDir, "case_info.json"))
  const configPath = caseInfo?.config ?? ""
  const expectedAnswer = configPath.includes("opencode_mock_c_fs")
    ? "Verification: verification receipt present"
    : "two"
  const afterAnswerTextPath = path.join(caseDir, "observer_text", "after-answer.txt")
  const afterAnswerText = await readText(afterAnswerTextPath)
  if (afterAnswerText.includes(expectedAnswer) === false) {
    anomalies.push({
      id: "emulator-final-answer-missing",
      message: `Expected emulator after-answer snapshot to include ${JSON.stringify(expectedAnswer)}.`,
    })
  }
  const snapshots = await readText(path.join(caseDir, "emulator_snapshots.txt"))
  if (snapshots.includes("# after-submit") === false || snapshots.includes("# after-answer") === false) {
    anomalies.push({
      id: "emulator-snapshots-missing-labels",
      message: "Expected emulator_snapshots.txt to include after-submit and after-answer labels.",
    })
  }
  const metadata = await readJson<Record<string, unknown>>(path.join(caseDir, "emulator_metadata.json"))
  if ((metadata?.display == null) || (metadata?.paneId == null)) {
    anomalies.push({
      id: "emulator-metadata-incomplete",
      message: "Expected emulator metadata to include display and paneId.",
    })
  }
  return anomalies
}

const run = async () => {
  const { caseDir } = parseArgs()
  const anomalies = await evaluateLiveWrapperEmulatorSmoke(caseDir)
  process.stdout.write(`${JSON.stringify(anomalies, null, 2)}\n`)
}

if (import.meta.url === new URL(process.argv[1] ?? "", "file:").href) {
  void run().catch((error) => {
    console.error((error as Error).message)
    process.exit(1)
  })
}
