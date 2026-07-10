import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"
import { writeSnapshotFile, type Step } from "./harness/spectator.ts"
import { TerminalAdapterHarnessError, runTerminalAdapterHarness } from "./harness/terminalAdapter.ts"

const ROOT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

const timestamp = (): string => {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("")
}

const parseArgs = (): { outDir: string } => {
  const args = process.argv.slice(2)
  let outDir = path.join(ROOT_DIR, "artifacts", "qc_preflight_ghostty_native_export", timestamp())
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--out-dir") {
      outDir = path.resolve(args[++index] ?? outDir)
    }
  }
  return { outDir }
}

const writePreflightCommand = async (outDir: string): Promise<string> => {
  const commandPath = path.join(outDir, "preflight-command.sh")
  const lines = [
    "#!/usr/bin/env bash",
    "set -euo pipefail",
    "printf 'GHOSTTY_PREFLIGHT_READY\\n'",
    "for i in $(seq 1 180); do printf 'GHOSTTY_SCROLLBACK_LINE_%03d\\n' \"$i\"; done",
    "exec bash --noprofile --norc",
  ]
  await fs.writeFile(commandPath, `${lines.join("\n")}\n`, { encoding: "utf8", mode: 0o755 })
  return commandPath
}

const listFiles = async (dir: string): Promise<string[]> => {
  return (await fs.readdir(dir).catch(() => [])).sort()
}

const readOptional = async (file: string): Promise<string> => {
  try {
    return await fs.readFile(file, "utf8")
  } catch {
    return ""
  }
}

const run = async () => {
  const { outDir } = parseArgs()
  await fs.mkdir(outDir, { recursive: true })
  const commandPath = await writePreflightCommand(outDir)
  const steps: Step[] = [
    { action: "wait", ms: 9_000 },
    { action: "snapshot", label: "after-scrollback-seed" },
    { action: "type", text: "echo BB_KEY_INJECTION_PROBE" },
    { action: "press", key: "enter" },
    { action: "wait", ms: 3_000 },
    { action: "snapshot", label: "after-key-injection" },
    { action: "press", key: "ctrl+shift+o" },
    { action: "wait", ms: 2_000 },
    { action: "snapshot", label: "after-manual-scrollback-key" },
  ]

  const stateDumpPath = path.join(outDir, "state.ndjson")
  let result
  try {
    result = await runTerminalAdapterHarness({
      lane: "ghostty",
      steps,
      command: `bash ${commandPath}`,
      cwd: outDir,
      cols: 132,
      rows: 36,
      runtimeDir: outDir,
      envOverrides: {
        BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      },
      stateDumpPath,
      stateDumpMode: "summary",
      stateDumpRateMs: 200,
      captureScreenshots: true,
    })
  } catch (error) {
    if (error instanceof TerminalAdapterHarnessError) {
      result = error.result
    } else {
      throw error
    }
  }

  await writeSnapshotFile(result.snapshots, path.join(outDir, "terminal_snapshots.txt"))
  await fs.writeFile(path.join(outDir, "terminal_metadata.json"), `${JSON.stringify(result.metadata, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "terminal_frames.json"), `${JSON.stringify(result.frames, null, 2)}\n`, "utf8")
  await fs.writeFile(path.join(outDir, "visible_final.txt"), result.visibleTextFinal, "utf8")
  await fs.writeFile(path.join(outDir, "scrollback_final.txt"), result.scrollbackTextFinal, "utf8")

  const observerTextDir = path.join(outDir, "observer_text")
  const observerFiles = await listFiles(observerTextDir)
  const screenFiles = observerFiles.filter((file) => file.endsWith(".ghostty_screen.txt"))
  const scrollbackFiles = observerFiles.filter((file) => file.endsWith(".ghostty_scrollback.txt"))
  const screenText = (await Promise.all(screenFiles.map((file) => readOptional(path.join(observerTextDir, file))))).join("\n")
  const scrollbackText = (await Promise.all(scrollbackFiles.map((file) => readOptional(path.join(observerTextDir, file))))).join("\n")
  const exportLog = await readOptional(path.join(outDir, "observer_runtime", "ghostty-native-export", "open_log.tsv"))
  const combined = `${result.visibleTextFinal}\n${result.scrollbackTextFinal}\n${screenText}\n${scrollbackText}`

  const checks = [
    {
      id: "screen-export-produced",
      pass: screenFiles.length > 0 && screenText.includes("GHOSTTY_PREFLIGHT_READY"),
      detail: `${screenFiles.length} native screen export(s)`,
    },
    {
      id: "keyboard-injection-produced-visible-output",
      pass: combined.includes("BB_KEY_INJECTION_PROBE"),
      detail: "typed echo probe appears in exported terminal text",
    },
    {
      id: "scrollback-export-produced",
      pass: scrollbackFiles.length > 0 && scrollbackText.includes("GHOSTTY_SCROLLBACK_LINE_001"),
      detail: `${scrollbackFiles.length} native scrollback export(s)`,
    },
    {
      id: "xdg-open-shim-invoked",
      pass: exportLog.trim().length > 0,
      detail: `${exportLog.trim().split(/\r?\n/).filter(Boolean).length} xdg-open invocation(s)`,
    },
  ]
  const report = {
    outDir,
    checks,
    screenFiles,
    scrollbackFiles,
    exportLogLines: exportLog.trim().split(/\r?\n/).filter(Boolean),
  }
  await fs.writeFile(path.join(outDir, "ghostty_native_export_preflight_report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8")
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`)
  const failures = checks.filter((check) => !check.pass)
  if (failures.length > 0) {
    process.stderr.write(failures.map((check) => `[ghostty-native-export-preflight] ${check.id}: ${check.detail}`).join("\n"))
    process.stderr.write("\n")
    process.exit(1)
  }
  process.exit(0)
}

void run().catch((error) => {
  console.error(error instanceof Error ? error.stack ?? error.message : String(error))
  process.exit(1)
})
