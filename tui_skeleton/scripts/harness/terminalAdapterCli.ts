import { promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { loadStepsFromFile, writeSnapshotFile } from "./spectator.ts"
import { TerminalAdapterHarnessError, runTerminalAdapterHarness, type TerminalAdapterHarnessResult } from "./terminalAdapter.ts"

const parseArgs = () => {
  const args = process.argv.slice(2)
  let lane: "dry-run" | "wezterm" | "ghostty" | undefined
  let scriptPath: string | undefined
  let command = "bb repl"
  let cwd: string | undefined
  let configPath: string | undefined
  let baseUrl: string | undefined
  let runtimeDir: string | undefined
  let snapshotsPath: string | undefined
  let metadataPath: string | undefined
  let framesPath: string | undefined
  let inputLogPath: string | undefined
  let visibleTextPath: string | undefined
  let scrollbackTextPath: string | undefined
  let stateDumpPath: string | undefined
  const envOverrides: Record<string, string> = {}
  let cols = 120
  let rows = 36

  for (let i = 0; i < args.length; i += 1) {
    switch (args[i]) {
      case "--lane":
        lane = args[++i] as "dry-run" | "wezterm" | "ghostty"
        break
      case "--script":
        scriptPath = args[++i]
        break
      case "--cmd":
        command = args[++i]
        break
      case "--cwd":
        cwd = args[++i]
        break
      case "--config":
        configPath = args[++i]
        break
      case "--base-url":
        baseUrl = args[++i]
        break
      case "--runtime-dir":
        runtimeDir = args[++i]
        break
      case "--snapshots":
        snapshotsPath = args[++i]
        break
      case "--metadata":
        metadataPath = args[++i]
        break
      case "--frames":
        framesPath = args[++i]
        break
      case "--input-log":
        inputLogPath = args[++i]
        break
      case "--visible-text":
        visibleTextPath = args[++i]
        break
      case "--scrollback-text":
        scrollbackTextPath = args[++i]
        break
      case "--state-dump":
        stateDumpPath = args[++i]
        break
      case "--env": {
        const raw = args[++i] ?? ""
        const eq = raw.indexOf("=")
        if (eq <= 0) {
          throw new Error(`invalid --env value: ${raw}`)
        }
        envOverrides[raw.slice(0, eq)] = raw.slice(eq + 1)
        break
      }
      case "--cols":
        cols = Number(args[++i])
        break
      case "--rows":
        rows = Number(args[++i])
        break
      default:
        break
    }
  }

  if (
    !lane ||
    !scriptPath ||
    !runtimeDir ||
    !snapshotsPath ||
    !metadataPath ||
    !framesPath ||
    !inputLogPath ||
    !visibleTextPath ||
    !scrollbackTextPath ||
    !stateDumpPath
  ) {
    throw new Error("missing required terminal adapter CLI arguments")
  }

  return {
    lane,
    scriptPath,
    command,
    cwd,
    configPath,
    baseUrl,
    runtimeDir,
    snapshotsPath,
    metadataPath,
    framesPath,
    inputLogPath,
    visibleTextPath,
    scrollbackTextPath,
    stateDumpPath,
    envOverrides,
    cols,
    rows,
  }
}

const main = async () => {
  const args = parseArgs()
  const steps = await loadStepsFromFile(args.scriptPath)
  await fs.mkdir(args.runtimeDir, { recursive: true })
  await fs.mkdir(path.dirname(args.snapshotsPath), { recursive: true })
  await fs.mkdir(path.dirname(args.metadataPath), { recursive: true })
  await fs.mkdir(path.dirname(args.framesPath), { recursive: true })
  await fs.mkdir(path.dirname(args.inputLogPath), { recursive: true })
  await fs.mkdir(path.dirname(args.visibleTextPath), { recursive: true })
  await fs.mkdir(path.dirname(args.scrollbackTextPath), { recursive: true })
  await fs.mkdir(path.dirname(args.stateDumpPath), { recursive: true })

  const writeResult = async (result: TerminalAdapterHarnessResult) => {
    await writeSnapshotFile(result.snapshots, args.snapshotsPath)
    await fs.writeFile(args.metadataPath, `${JSON.stringify(result.metadata, null, 2)}\n`, "utf8")
    await fs.writeFile(args.framesPath, `${JSON.stringify(result.frames, null, 2)}\n`, "utf8")
    await fs.writeFile(args.inputLogPath, `${result.inputLogLines.join("\n")}\n`, "utf8")
    await fs.writeFile(args.visibleTextPath, result.visibleTextFinal, "utf8")
    await fs.writeFile(args.scrollbackTextPath, result.scrollbackTextFinal, "utf8")
  }

  const runOptions = {
    lane: args.lane,
    steps,
    command: args.command,
    cwd: args.cwd,
    configPath: args.configPath,
    baseUrl: args.baseUrl,
    cols: args.cols,
    rows: args.rows,
    runtimeDir: args.runtimeDir,
    envOverrides: args.envOverrides,
    stateDumpPath: args.stateDumpPath,
    stateDumpMode: "summary",
    stateDumpRateMs: 200,
    captureScreenshots: true,
    maxDurationMs: 240_000,
  } as const

  let result: TerminalAdapterHarnessResult
  try {
    result = await runTerminalAdapterHarness(runOptions)
  } catch (error) {
    if (error instanceof TerminalAdapterHarnessError) {
      await writeResult(error.result)
    }
    throw error
  }

  await writeResult(result)

  console.log(`Captured ${result.snapshots.length} snapshot(s) via ${args.lane}`)
  console.log(`Snapshots written to ${args.snapshotsPath}`)
}

main()
  .then(() => {
    process.exit(0)
  })
  .catch((error) => {
    console.error("Terminal adapter CLI failed:", error)
    process.exit(1)
  })
