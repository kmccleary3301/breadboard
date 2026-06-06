import { spawn, type ChildProcess } from "node:child_process"
import { createWriteStream, promises as fs } from "node:fs"
import path from "node:path"
import process from "node:process"
import { fileURLToPath } from "node:url"
import { createHash } from "node:crypto"
import {
  loadStepsFromFile,
  loadWinchEventsFromFile,
  SpectatorHarnessError,
  runSpectatorHarness,
  writeSnapshotFile,
} from "./harness/spectator.ts"
import {
  EmulatorObserverHarnessError,
  runWeztermObserverHarness,
} from "./harness/weztermObserver.ts"
import {
  TerminalAdapterHarnessError,
  runTerminalAdapterHarness,
  type TerminalAdapterLane,
  type TerminalAdapterStepEvent,
} from "./harness/terminalAdapter.ts"
import { renderGridFromFrames } from "../tools/tty/vtgrid.ts"
import { runLayoutAssertions, type LayoutAnomaly } from "../tools/assertions/layoutChecks.ts"
import { computeGridDiff, readGridLines } from "../tools/tty/gridDiff.ts"
import { buildTimelineArtifacts } from "../tools/timeline/buildTimeline.ts"
import { buildFlamegraph } from "../tools/timeline/flamegraph.ts"
import { buildTtyDoc } from "../tools/reports/ttydoc.ts"
import { buildBatchTtyDoc } from "../tools/reports/ttydocBatch.ts"
import { runClipboardAssertions, ClipboardAssertions } from "../tools/assertions/clipboardChecks.ts"
import { runContractChecks, type ContractOptions } from "../tools/assertions/contractChecks.ts"
import { startReplayServer } from "../tools/mock/replaySse.ts"
import { buildClipboardDiffReport } from "../tools/reports/clipboardDiffReport.ts"
import { buildKeyFuzzReport } from "../tools/reports/keyFuzzReport.ts"
import { buildManifestHashes } from "../tools/reports/manifestHasher.ts"
import { shutdownEngine } from "../src/engine/engineSupervisor.ts"
import { evaluateBatchManifestSchema } from "../tools/assertions/batchManifestSchemaCheck.ts"
import { evaluateLiveProvenance } from "../tools/assertions/liveProvenanceCheck.ts"
import { resolveCaseCommandCwd as resolveCaseCommandCwdForHarness } from "./harness/caseCommandCwd.ts"
import { resolveStressCaseConfigPath } from "./harness/caseConfigPath.ts"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const ROOT_DIR = path.resolve(__dirname, "..")
const REPO_ROOT = path.resolve(ROOT_DIR, "..")
process.chdir(ROOT_DIR)
const REPRO_CWD = path.basename(ROOT_DIR)
const SSE_WAIT_TIMEOUT_MS = 45_000
const SSE_REPLAY_TIMEOUT_MS = 4_000
const DEFAULT_KEEP_RUNS = 3

const resolveRepoPath = (value: string): string => {
  if (path.isAbsolute(value)) return value
  if (value.startsWith("./") || value.startsWith("../")) {
    return path.resolve(ROOT_DIR, value)
  }
  return path.resolve(REPO_ROOT, value)
}

const resolveTuiPath = (value: string): string => {
  if (path.isAbsolute(value)) return value
  return path.resolve(ROOT_DIR, value)
}

const resolveCaseCommandCwd = (testCase: StressCase, command: string): string =>
  resolveCaseCommandCwdForHarness({
    testCase,
    command,
    rootDir: ROOT_DIR,
    repoRoot: REPO_ROOT,
    dummyWorkspaceCwd: QC_DUMMY_WORKSPACE_CWD,
    allowProjectCwd: process.env.BREADBOARD_ALLOW_PROJECT_CWD === "1",
  })

const writeCaseWorkspaceFiles = async (
  commandCwd: string,
  files: ReadonlyArray<{ readonly path: string; readonly contents: string }> | undefined,
): Promise<void> => {
  if (!files || files.length === 0) return
  for (const file of files) {
    const relativePath = file.path.trim()
    if (!relativePath || path.isAbsolute(relativePath)) {
      throw new Error(`Invalid workspace fixture path: ${file.path}`)
    }
    const resolved = path.resolve(commandCwd, relativePath)
    const rel = path.relative(commandCwd, resolved)
    if (rel.startsWith("..") || path.isAbsolute(rel)) {
      throw new Error(`Workspace fixture escapes case cwd: ${file.path}`)
    }
    await fs.mkdir(path.dirname(resolved), { recursive: true })
    await fs.writeFile(resolved, file.contents, "utf8")
  }
}

const DEFAULT_CONFIG = resolveRepoPath(
  process.env.CONFIG_PATH ?? process.env.STRESS_CONFIG ?? "../agent_configs/misc/opencode_cli_mock_guardrails.yaml",
)
const DEFAULT_LIVE_CONFIG = resolveRepoPath(
  process.env.BREADBOARD_LIVE_CONFIG || "../agent_configs/misc/opencode_openai_gpt5nano_c_fs_cli_shared.yaml",
)
const DEFAULT_BASE_URL = process.env.BASE_URL ?? process.env.BREADBOARD_API_URL ?? "http://127.0.0.1:9099"
const DEFAULT_COMMAND = "node dist/main.js repl"
const QC_DUMMY_WORKSPACE_CWD = "/tmp/bb_qc_terminal_dummy_workspace"
const QC_GHOSTTY_TMUX_WORKSPACE_CWD = "/tmp/bb_qc_ghostty_tmux_workspace"
const REPL_SCRIPT_MAX_DURATION_MS = Number(process.env.STRESS_SCRIPT_MAX_MS ?? 240_000)
const LARGE_CLIPBOARD_PAYLOAD = "Large clipboard payload chunk ".repeat(40)
const DEFAULT_LIVE_BOOT_TIMEOUT_MS = 15_000
const LIVE_HEALTH_PATH = "/health"

const readCommandOutput = async (argv: string[], cwd: string): Promise<string | null> => {
  return await new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), { cwd, stdio: ["ignore", "pipe", "pipe"] })
    let stdout = ""
    child.stdout?.on("data", (chunk) => (stdout += String(chunk)))
    child.on("error", () => resolve(null))
    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim())
      } else {
        resolve(null)
      }
    })
  })
}

const runPostCheckScript = async (scriptPath: string, caseDir: string): Promise<LayoutAnomaly[]> => {
  const resolved = resolveTuiPath(scriptPath)
  return await new Promise((resolve, reject) => {
    const child = spawn("pnpm", ["exec", "tsx", resolved, "--case-dir", caseDir], {
      cwd: ROOT_DIR,
      stdio: ["ignore", "pipe", "pipe"],
    })
    let stdout = ""
    let stderr = ""
    child.stdout?.on("data", (chunk) => (stdout += String(chunk)))
    child.stderr?.on("data", (chunk) => (stderr += String(chunk)))
    child.on("error", reject)
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`post-check exited with code ${code}: ${stderr.trim() || stdout.trim()}`))
        return
      }
      try {
        const parsed = JSON.parse(stdout || "[]") as LayoutAnomaly[]
        resolve(Array.isArray(parsed) ? parsed : [])
      } catch (error) {
        reject(new Error(`post-check returned invalid JSON: ${(error as Error).message}`))
      }
    })
  })
}

const splitCommand = (command: string): { executable: string; args: string[] } => {
  const parts = command.split(" ").filter((part) => part.length > 0)
  return {
    executable: parts[0] ?? "",
    args: parts.slice(1),
  }
}

const resolveExecutablePath = async (executable: string, cwd: string): Promise<string | null> => {
  if (!executable) return null
  if (executable.includes("/") || executable.startsWith(".")) {
    const absolute = path.isAbsolute(executable) ? executable : path.resolve(cwd, executable)
    try {
      await fs.access(absolute)
      return absolute
    } catch {
      return null
    }
  }
  const pathValue = process.env.PATH ?? ""
  for (const dir of pathValue.split(path.delimiter)) {
    if (!dir) continue
    const candidate = path.join(dir, executable)
    try {
      await fs.access(candidate)
      return candidate
    } catch {
      continue
    }
  }
  return null
}

const buildCommandProvenance = async (command: string, cwd: string) => {
  const { executable, args } = splitCommand(command)
  const resolvedExecutable = await resolveExecutablePath(executable, cwd)
  const realpath = resolvedExecutable ? await fs.realpath(resolvedExecutable).catch(() => resolvedExecutable) : null
  return {
    command,
    executable,
    args,
    cwd,
    resolvedExecutable,
    realpath,
  }
}

const buildHealthUrl = (baseUrl: string): string => {
  try {
    const url = new URL(baseUrl)
    url.pathname = LIVE_HEALTH_PATH
    url.search = ""
    url.hash = ""
    return url.toString()
  } catch {
    return `${baseUrl.replace(/\/+$/, "")}${LIVE_HEALTH_PATH}`
  }
}

const checkHealth = async (baseUrl: string): Promise<boolean> => {
  const url = buildHealthUrl(baseUrl)
  try {
    const response = await fetch(url, { method: "GET" })
    return response.ok
  } catch {
    return false
  }
}

const fetchJson = async <T>(url: string, timeoutMs = 5_000): Promise<T | null> => {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(url, { method: "GET", signal: controller.signal })
    if (!response.ok) return null
    return (await response.json().catch(() => null)) as T | null
  } catch {
    return null
  } finally {
    clearTimeout(timeout)
  }
}

const buildStatusUrl = (baseUrl: string): string => {
  try {
    const url = new URL(baseUrl)
    url.pathname = "/status"
    url.search = ""
    url.hash = ""
    return url.toString()
  } catch {
    return `${baseUrl.replace(/\/+$/, "")}/status`
  }
}

const readLiveBridgeProvenance = async (baseUrl: string): Promise<Record<string, unknown> | null> => {
  const [health, status] = await Promise.all([
    fetchJson<Record<string, unknown>>(buildHealthUrl(baseUrl)),
    fetchJson<Record<string, unknown>>(buildStatusUrl(baseUrl)),
  ])
  if (!health && !status) return null
  return { health, status }
}

const waitForBridgeExit = async (baseUrl: string, timeoutMs: number): Promise<boolean> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (!(await checkHealth(baseUrl))) {
      return true
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  return false
}

const shutdownLiveBridge = async (baseUrl: string, timeoutMs = 5_000): Promise<boolean> => {
  const status = await fetchJson<Record<string, unknown>>(buildStatusUrl(baseUrl), 2_000)
  const pid = typeof status?.pid === "number" ? status.pid : null
  if (!pid) {
    return !(await checkHealth(baseUrl))
  }
  try {
    process.kill(pid, "SIGTERM")
  } catch {
    return !(await checkHealth(baseUrl))
  }
  if (await waitForBridgeExit(baseUrl, timeoutMs)) {
    return true
  }
  try {
    process.kill(pid, "SIGKILL")
  } catch {
    // ignore
  }
  return await waitForBridgeExit(baseUrl, 2_000)
}

const waitForHealth = async (baseUrl: string, timeoutMs: number): Promise<boolean> => {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (await checkHealth(baseUrl)) {
      return true
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

const spawnLiveBridge = (baseUrl: string): ChildProcess => {
  const url = new URL(baseUrl)
  const host = url.hostname || "127.0.0.1"
  const port = url.port || process.env.BREADBOARD_CLI_PORT || "9099"
  const env = {
    ...process.env,
    BREADBOARD_CLI_HOST: host,
    BREADBOARD_CLI_PORT: String(port),
  }
  return spawn("python", ["-m", "agentic_coder_prototype.api.cli_bridge.server"], {
    cwd: path.resolve(ROOT_DIR, ".."),
    env,
    stdio: "inherit",
  })
}

const ensureLiveBridge = async (baseUrl: string, autoStart: boolean): Promise<ChildProcess | null> => {
  if (await checkHealth(baseUrl)) {
    return null
  }
  if (!autoStart) {
    throw new Error(
      `[stress] Live engine is not reachable at ${baseUrl}. Start the CLI bridge or re-run with --auto-start-live.`,
    )
  }
  const child = spawnLiveBridge(baseUrl)
  const ok = await waitForHealth(baseUrl, DEFAULT_LIVE_BOOT_TIMEOUT_MS)
  if (!ok) {
    try {
      child.kill("SIGTERM")
    } catch {
      // ignore
    }
    throw new Error(`[stress] Live engine did not become healthy within ${DEFAULT_LIVE_BOOT_TIMEOUT_MS}ms.`)
  }
  return child
}

const captureTmuxPane = async (
  target: string,
  batchDir: string,
  scale: number,
): Promise<{ png: string; ansi: string; text: string } | null> => {
  const scriptPath = path.resolve(ROOT_DIR, "..", "scripts", "tmux_capture_to_png.py")
  try {
    await fs.access(scriptPath)
  } catch {
    console.warn(`[stress] tmux capture script missing at ${scriptPath}`)
    return null
  }
  const safeTarget = target.replace(/[^A-Za-z0-9_.-]+/g, "_")
  const outPath = path.join(batchDir, `tmux_capture_${safeTarget}.png`)
  await new Promise<void>((resolve, reject) => {
    const argv = [scriptPath, "--target", target, "--out", outPath]
    if (Math.abs((scale ?? 1) - 1) > 1e-6) {
      argv.push("--render-profile", "legacy", "--scale", String(scale))
    }
    const child = spawn(
      "python",
      argv,
      { cwd: path.resolve(ROOT_DIR, ".."), stdio: "inherit" },
    )
    child.on("close", (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`tmux capture exited with code ${code}`))
      }
    })
    child.on("error", (error) => reject(error))
  })
  return {
    png: outPath,
    ansi: outPath.replace(/\.png$/, ".ansi"),
    text: outPath.replace(/\.png$/, ".txt"),
  }
}

const readGitInfo = async () => {
  const [commit, branch, status] = await Promise.all([
    readCommandOutput(["git", "rev-parse", "HEAD"], ROOT_DIR),
    readCommandOutput(["git", "rev-parse", "--abbrev-ref", "HEAD"], ROOT_DIR),
    readCommandOutput(["git", "status", "--porcelain"], ROOT_DIR),
  ])
  if (!commit && !branch) return null
  return {
    commit,
    branch,
    dirty: Boolean(status && status.length > 0),
  }
}

const triggerMockPlayback = async (baseUrl: string, sessionId: string) => {
  try {
    const url = new URL(`/sessions/${sessionId}/input`, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: "" }),
    })
  } catch {
    // ignore mock playback trigger errors
  }
}

const readBridgeChaosFromEnv = () => {
  const latency = Number(process.env.BREADBOARD_CLI_LATENCY_MS ?? "0")
  const jitter = Number(process.env.BREADBOARD_CLI_JITTER_MS ?? "0")
  const drop = Number(process.env.BREADBOARD_CLI_DROP_RATE ?? "0")
  if (!latency && !jitter && !drop) {
    return null
  }
  return {
    mode: "bridge",
    latencyMs: latency || 0,
    jitterMs: jitter || 0,
    dropRate: drop || 0,
  }
}

const mergeChaosInfo = (
  base: Record<string, unknown> | null | undefined,
  extra: Record<string, unknown> | null | undefined,
): Record<string, unknown> | null => {
  if (base && extra) {
    return { ...base, ...extra }
  }
  return base ?? extra ?? null
}

const writeEventsNdjson = async (ssePath: string, targetPath: string) => {
  let contents = ""
  try {
    contents = await fs.readFile(ssePath, "utf8")
  } catch {
    await fs.writeFile(targetPath, "", "utf8").catch(() => undefined)
    return
  }
  const lines = contents.split(/\r?\n/)
  const events: unknown[] = []
  let dataLines: string[] = []
  const flush = () => {
    if (dataLines.length === 0) return
    const payload = dataLines.join("\n")
    dataLines = []
    try {
      const parsed = JSON.parse(payload)
      events.push(parsed)
    } catch {
      // ignore malformed JSON payloads
    }
  }
  for (const line of lines) {
    if (!line) {
      flush()
      continue
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart())
      continue
    }
    if (line.startsWith(":")) {
      continue
    }
    if (line.startsWith("event:") || line.startsWith("id:") || line.startsWith("retry:")) {
      continue
    }
  }
  flush()
  const out = events.map((event) => JSON.stringify(event)).join("\n")
  await fs.writeFile(targetPath, out.length > 0 ? `${out}\n` : "", "utf8")
}

const isBatchDirName = (value: string): boolean => /^\d{8}-\d{6}$/.test(value)

const pruneOldBatches = async (outDir: string, keepCount: number) => {
  if (!Number.isFinite(keepCount) || keepCount <= 0) return
  const entries = await fs.readdir(outDir, { withFileTypes: true })
  const batchNames = entries
    .filter((entry) => entry.isDirectory() && isBatchDirName(entry.name))
    .map((entry) => entry.name)
    .sort()
  if (batchNames.length <= keepCount) return
  const toRemove = batchNames.slice(0, Math.max(0, batchNames.length - keepCount))
  await Promise.all(
    toRemove.map(async (name) => {
      const dirPath = path.join(outDir, name)
      const zipPath = path.join(outDir, `${name}.zip`)
      await fs.rm(dirPath, { recursive: true, force: true }).catch(() => undefined)
      await fs.rm(zipPath, { force: true }).catch(() => undefined)
    }),
  )
  console.log(`[stress] pruned ${toRemove.length} old bundle(s); kept ${keepCount}.`)
}

interface BaseCase {
  readonly id: string
  readonly script: string
  readonly description: string
  readonly mockSseScript?: string
  readonly env?: Record<string, string>
  readonly configPath?: string
  readonly command?: string
  readonly cwd?: string
  readonly baseUrl?: string
  readonly requiresLive?: boolean
  readonly postCheckScript?: string
  readonly workspaceFiles?: ReadonlyArray<{
    readonly path: string
    readonly contents: string
  }>
}

interface ReplCase extends BaseCase {
  readonly kind: "repl"
  readonly model?: string
}

interface PtyCase extends BaseCase {
  readonly kind: "pty"
  readonly cols?: number
  readonly rows?: number
  readonly clipboardText?: string
  readonly clipboardFile?: string
  readonly submitTimeoutMs?: number
  readonly clipboardAssertions?: ClipboardAssertions
  readonly winchScript?: string
  readonly expectedAltText?: string
}

interface EmulatorCase extends BaseCase {
  readonly kind: "emulator"
  readonly cols?: number
  readonly rows?: number
  readonly submitTimeoutMs?: number
}

interface TerminalCase extends BaseCase {
  readonly kind: "terminal"
  readonly terminalLane: TerminalAdapterLane
  readonly cols?: number
  readonly rows?: number
  readonly submitTimeoutMs?: number
}

type StressCase = ReplCase | PtyCase | EmulatorCase | TerminalCase

const STRESS_CASES: StressCase[] = [
  {
    id: "mock_hello",
    kind: "repl",
    script: "scripts/mock_hello_script.json",
    description: "Happy-path hello world run",
    mockSseScript: "scripts/mock_sse_sample.json",
  },
  {
    id: "mock_multi_tool",
    kind: "repl",
    script: "scripts/mock_multi_tool_script.json",
    description: "Deterministic mock stream with tool + diff events",
    mockSseScript: "scripts/mock_sse_diff.json",
  },
  {
    id: "modal_overlay",
    kind: "repl",
    script: "scripts/modal_overlay_stress.json",
    description: "Modal overlay focus + dismissal",
    mockSseScript: "scripts/mock_sse_modal_overlay.json",
  },
  {
    id: "resize_storm",
    kind: "pty",
    script: "scripts/resize_storm.json",
    description: "Repeated terminal resize events (PTY harness)",
    winchScript: "scripts/resize_storm_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "resize_storm_modal",
    kind: "pty",
    script: "scripts/resize_storm_modal_pty.json",
    description: "Resize storm with modal overlay open",
    winchScript: "scripts/resize_storm_modal_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: "scripts/mock_model_catalog.json",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "narrow_terminal",
    kind: "pty",
    script: "scripts/narrow_terminal_pty.json",
    description: "Narrow terminal layout snapshot (80x24)",
    winchScript: "scripts/narrow_terminal_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "narrow_terminal_small",
    kind: "pty",
    script: "scripts/narrow_terminal_small_pty.json",
    description: "Narrow terminal layout snapshot (60x18)",
    winchScript: "scripts/narrow_terminal_small_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "narrow_terminal_tiny",
    kind: "pty",
    script: "scripts/narrow_terminal_tiny_pty.json",
    description: "Narrow terminal layout snapshot (50x16)",
    winchScript: "scripts/narrow_terminal_tiny_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "layout_ordering",
    kind: "pty",
    script: "scripts/layout_ordering_pty.json",
    description: "Layout ordering / resize probe via PTY harness",
    mockSseScript: "scripts/mock_sse_layout_ordering.json",
    submitTimeoutMs: 0,
  },
  {
    id: "history_ring",
    kind: "pty",
    script: "scripts/history_ring_pty.json",
    description: "History ring wrap behavior",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "markdown_table",
    kind: "pty",
    script: "scripts/markdown_table_pty.json",
    description: "Markdown table rendering",
    mockSseScript: "scripts/mock_sse_markdown_table.json",
  },
  {
    id: "markdown_mdx",
    kind: "pty",
    script: "scripts/markdown_mdx_pty.json",
    description: "MDX fallback rendering",
    mockSseScript: "scripts/mock_sse_markdown_mdx.json",
  },
  {
    id: "attachment_remove",
    kind: "pty",
    script: "scripts/attachment_remove_pty.json",
    description: "Attachment removal (Backspace) UX",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "tool_ordering",
    kind: "pty",
    script: "scripts/tool_ordering_pty.json",
    description: "Tool ordering snapshot",
    mockSseScript: "scripts/mock_sse_diff.json",
    submitTimeoutMs: 0,
  },
  {
    id: "tool_ordering_edge",
    kind: "pty",
    script: "scripts/tool_ordering_edge_pty.json",
    description: "Tool ordering edge case (interleaved tool_result)",
    mockSseScript: "scripts/mock_sse_tool_order_edge.json",
    submitTimeoutMs: 0,
  },
  {
    id: "ime_placeholder",
    kind: "pty",
    script: "scripts/ime_placeholder_pty.json",
    description: "IME placeholder coverage (multi-byte input)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "permission_error",
    kind: "pty",
    script: "scripts/permission_error_pty.json",
    description: "Permission error banner (command timeout)",
    mockSseScript: "scripts/mock_sse_permission_error.json",
    submitTimeoutMs: 0,
  },
  {
    id: "network_error",
    kind: "pty",
    script: "scripts/p6_wezterm_escalated_network_error_pty.json",
    description: "Network error banner (mock stream ends)",
    mockSseScript: "scripts/mock_sse_network_error.json",
    submitTimeoutMs: 0,
  },
  {
    id: "permission_rewind",
    kind: "pty",
    script: "scripts/permission_rewind_pty.json",
    description: "Permission approval modal + rewind checkpoints flow",
    mockSseScript: "scripts/mock_sse_permission_rewind.json",
    submitTimeoutMs: 0,
  },
  {
    id: "rewind_paging",
    kind: "pty",
    script: "scripts/rewind_paging_pty.json",
    description: "Rewind paging (PgUp/PgDn) behavior",
    mockSseScript: "scripts/mock_sse_permission_rewind.json",
    submitTimeoutMs: 0,
  },
  {
    id: "todos_panel",
    kind: "pty",
    script: "scripts/todos_pty.json",
    description: "Todos overlay + transcript command",
    mockSseScript: "scripts/mock_sse_todos.json",
    submitTimeoutMs: 0,
  },
  {
    id: "tasks_panel",
    kind: "pty",
    script: "scripts/tasks_panel_pty.json",
    description: "Tasks panel open/close",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "multiagent_tasks_panel",
    kind: "pty",
    script: "scripts/multiagent_tasks_panel_pty.json",
    description: "Production-path multiagent taskboard with work-graph lanes, strip summary, and resize recovery",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 128,
    rows: 34,
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
  },
  {
    id: "multiagent_task_focus_controls",
    kind: "pty",
    script: "scripts/multiagent_task_focus_controls_pty.json",
    description: "Production-path multiagent taskboard filters, focus view, tail loading, raw/snippet toggle, follow toggle, and resize recovery",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_02 bind localhost\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_03 listen\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_04 accept client\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_05 parse HELO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_06 parse MAIL FROM\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_07 parse RCPT TO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_08 parse DATA\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_09 persist eml\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_10 graceful QUIT\"}",
          "{\"event\":\"summary\",\"message\":\"SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskFocusControlsCheck.ts",
  },
  {
    id: "multiagent_task_slash_inspect_follow",
    kind: "pty",
    script: "scripts/multiagent_task_slash_inspect_follow_pty.json",
    description: "Production-path slash task inspection and task follow controls: /inspect task plus /follow task pause/status/resume stay local and resize-safe",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_02 bind localhost\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_03 listen\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_04 accept client\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_05 parse HELO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_06 parse MAIL FROM\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_07 parse RCPT TO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_08 parse DATA\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_09 persist eml\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_10 graceful QUIT\"}",
          "{\"event\":\"summary\",\"message\":\"SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskSlashInspectFollowCheck.ts",
  },
  {
    id: "multiagent_task_log_export",
    kind: "pty",
    script: "scripts/multiagent_task_log_export_pty.json",
    description: "Production-path Task Focus export writes selected raw task log to a durable artifact without flooding the main transcript",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_02 bind localhost\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_03 listen\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_04 accept client\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_05 parse HELO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_06 parse MAIL FROM\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_07 parse RCPT TO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_08 parse DATA\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_09 persist eml\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_10 graceful QUIT\"}",
          "{\"event\":\"summary\",\"message\":\"SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskLogExportCheck.ts",
  },
  {
    id: "multiagent_failure_retry",
    kind: "pty",
    script: "scripts/multiagent_failure_retry_pty.json",
    description: "Production-path failed multiagent task UX: failed filter, error detail, artifact tail, raw-log separation, resize recovery, and close behavior",
    mockSseScript: "scripts/mock_sse_multiagent_failure.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-tester-fail-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"Tester started SMTP negative path\"}",
          "{\"event\":\"tool\",\"name\":\"smtp_smoke_test\",\"message\":\"FAILURE_TAIL_LINE_01 connect localhost\"}",
          "{\"event\":\"tool\",\"name\":\"smtp_smoke_test\",\"message\":\"FAILURE_TAIL_LINE_02 send HELO\"}",
          "{\"event\":\"tool\",\"name\":\"smtp_smoke_test\",\"message\":\"FAILURE_TAIL_LINE_03 missing DATA terminator\"}",
          "{\"event\":\"error\",\"retryable\":true,\"message\":\"FAILURE_TAIL_LINE_04 SMTP_TEST_FAILURE missing DATA terminator\"}",
          "{\"event\":\"summary\",\"message\":\"FAILURE_TAIL_LINE_05 deterministic failure marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentFailureRetryCheck.ts",
  },
  {
    id: "multiagent_task_action_guards",
    kind: "pty",
    script: "scripts/multiagent_task_action_guards_pty.json",
    description: "Production-path taskboard action guard UX: per-task mutation controls are visible, keyboard-addressable, and explicitly unavailable until engine endpoints exist",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_02 bind localhost\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_03 listen\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_04 accept client\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_05 parse HELO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_06 parse MAIL FROM\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_07 parse RCPT TO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_08 parse DATA\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_09 persist eml\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_10 graceful QUIT\"}",
          "{\"event\":\"summary\",\"message\":\"SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskActionGuardsCheck.ts",
  },
  {
    id: "multiagent_task_action_command",
    kind: "pty",
    script: "scripts/multiagent_task_action_command_pty.json",
    description: "Production-path Task Focus action dispatch: engine-backed task_action command updates selected task state without polluting the transcript",
    mockSseScript: "scripts/mock_sse_multiagent_task_action_ack.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"summary\",\"message\":\"TASK_ACTION_CANCEL_ACK deterministic marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASK_ACTIONS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASK_ACTIONS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskActionCommandCheck.ts",
  },
  {
    id: "agents_taskboard_entrypoint",
    kind: "pty",
    script: "scripts/agents_taskboard_entrypoint_pty.json",
    description: "Experimental /agents command opens the bounded production-path multiagent task dashboard without pretending create/control endpoints exist",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    cols: 132,
    rows: 36,
    env: {
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/agentsTaskboardEntrypointCheck.ts",
  },
  {
    id: "ctree_summary",
    kind: "pty",
    script: "scripts/ctree_summary_pty.json",
    description: "CTree summary line in tasks panel",
    mockSseScript: "scripts/mock_sse_ctree.json",
    submitTimeoutMs: 0,
  },
  {
    id: "usage_modal",
    kind: "pty",
    script: "scripts/usage_modal_pty.json",
    description: "Usage modal open/close",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "usage_metrics",
    kind: "pty",
    script: "scripts/usage_metrics_pty.json",
    description: "Usage metrics summary line",
    mockSseScript: "scripts/mock_sse_usage.json",
    submitTimeoutMs: 0,
  },
  {
    id: "streaming_markdown",
    kind: "pty",
    script: "scripts/p6_wezterm_escalated_streaming_smoke_pty.json",
    description: "Streaming markdown render updates",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
  },
  {
    id: "follow_pause_resume",
    kind: "pty",
    script: "scripts/follow_pause_resume_pty.json",
    description: "Explicit pause-follow and resume over a slow markdown stream",
    mockSseScript: "scripts/mock_sse_follow_pause_resume.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/followPauseResumeCheck.ts",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
  },
  {
    id: "spine_streaming_smoke",
    kind: "pty",
    script: "scripts/streaming_markdown_pty.json",
    description: "Known-good QC spine smoke for remote markdown streaming",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/debugStreamCorrelationCheck.ts",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
  },
  {
    id: "spine_mermaid_fallback",
    kind: "pty",
    script: "scripts/mermaid_fallback_pty.json",
    description: "Canonical mermaid fenced-block fallback rendering smoke",
    mockSseScript: "scripts/mock_sse_mermaid_fallback.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/mermaidFallbackCheck.ts",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
  },
  {
    id: "file_picker",
    kind: "pty",
    script: "scripts/file_picker_pty.json",
    description: "`@` file picker tree traversal + mention insertion",
    mockSseScript: "scripts/mock_sse_file_picker.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_TUI_FILE_PICKER_MODE: "tree",
    },
  },
  {
    id: "file_picker_fuzzy",
    kind: "pty",
    script: "scripts/file_picker_fuzzy_pty.json",
    description: "`@` file picker fuzzy search + selection",
    mockSseScript: "scripts/mock_sse_file_picker.json",
    submitTimeoutMs: 0,
  },
  {
    id: "skills_picker",
    kind: "pty",
    script: "scripts/skills_picker_pty.json",
    description: "Skills picker open + selection apply",
    mockSseScript: "scripts/mock_sse_skills.json",
    submitTimeoutMs: 0,
  },
  {
    id: "skills_picker_ctrl_g",
    kind: "pty",
    script: "scripts/skills_picker_ctrl_g_pty.json",
    description: "Skills picker open via Ctrl+G",
    mockSseScript: "scripts/mock_sse_skills.json",
    submitTimeoutMs: 0,
  },
  {
    id: "file_picker_large",
    kind: "pty",
    script: "scripts/file_picker_large_pty.json",
    description: "`@` file picker large file gating hint",
    mockSseScript: "scripts/mock_sse_file_mentions.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_TUI_FILE_MENTION_MAX_INLINE_BYTES_PER_FILE: "1",
    },
  },
  {
    id: "file_picker_truncated",
    kind: "pty",
    script: "scripts/file_picker_truncated_pty.json",
    description: "`@` file picker truncated index hint",
    mockSseScript: "scripts/mock_sse_file_picker.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_TUI_FILE_PICKER_MAX_INDEX_FILES: "3",
    },
  },
  {
    id: "file_mentions",
    kind: "pty",
    script: "scripts/file_mentions_pty.json",
    description: "`@` file mention submission attaches file contents (with truncation gating)",
    mockSseScript: "scripts/mock_sse_file_mentions.json",
    env: {
      BREADBOARD_TUI_FILE_MENTION_MAX_INLINE_BYTES_PER_FILE: "1",
      BREADBOARD_TUI_FILE_MENTION_MAX_INLINE_BYTES_TOTAL: "1",
    },
  },
  {
    id: "slash_menu",
    kind: "pty",
    script: "scripts/slash_menu_pty.json",
    description: "Slash command list + filter + tab completion",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_TUI_CHROME: "claude",
    },
  },
  {
    id: "slash_menu_resize",
    kind: "pty",
    script: "scripts/slash_menu_resize_pty.json",
    description: "Slash menu stays stable across resize",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_TUI_CHROME: "claude",
    },
  },
  {
    id: "shortcuts_overlay",
    kind: "pty",
    script: "scripts/shortcuts_overlay_pty.json",
    description: "Shortcuts overlay capture",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    winchScript: "scripts/shortcuts_overlay_winch.json",
    env: {
      BREADBOARD_SHORTCUTS_STICKY: "1",
    },
  },
  {
    id: "transcript_viewer_toggle",
    kind: "pty",
    script: "scripts/transcript_viewer_toggle_pty.json",
    description: "Ctrl+T transcript viewer toggle (alt-screen)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    expectedAltText: "Mock assistant: hello world",
  },
  {
    id: "transcript_viewer_search",
    kind: "pty",
    script: "scripts/transcript_viewer_search_pty.json",
    description: "Transcript viewer search (`/` + n/p)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "transcript_viewer_navigation",
    kind: "pty",
    script: "scripts/transcript_viewer_navigation_pty.json",
    description: "Transcript viewer follow-tail and inspect navigation (g/G)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/transcriptViewerNavigationCheck.ts",
  },
  {
    id: "recent_sessions_overlay",
    kind: "pty",
    script: "scripts/recent_sessions_overlay_pty.json",
    description: "Recent-session re-entry overlay surface",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/recentSessionsOverlayCheck.ts",
  },
  {
    id: "collapsed_detail_inspection",
    kind: "pty",
    script: "scripts/collapsed_detail_inspection_pty.json",
    description: "Collapsed transcript detail inspection overlay",
    mockSseScript: "scripts/mock_sse_collapsed_detail.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/collapsedDetailInspectionCheck.ts",
  },
  {
    id: "transcript_result_actionability",
    kind: "pty",
    script: "scripts/transcript_result_actionability_pty.json",
    description: "Transcript-discovered result detail and artifact inspection",
    mockSseScript: "scripts/mock_sse_p3_result_actionability.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/transcriptResultActionabilityCheck.ts",
  },
  {
    id: "mixed_transcript_projection",
    kind: "pty",
    script: "scripts/p14_h5_tool_diff_approval_pty.json",
    description: "Mixed transcript projection across user, assistant, tool, diff, and approval cells",
    mockSseScript: "scripts/mock_sse_p14_h5_tool_diff_approval.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/mixedTranscriptProjectionCheck.ts",
  },
  {
    id: "composer_continuation_cues",
    kind: "pty",
    script: "scripts/composer_continuation_cues_pty.json",
    description: "Composer/footer continuation cue deck",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/composerContinuationCuesCheck.ts",
  },
  {
    id: "composer_command_surface",
    kind: "pty",
    script: "scripts/composer_command_surface_pty.json",
    description: "Composer slash-command continuation surface",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/composerCommandSurfaceCheck.ts",
  },
  {
    id: "command_argument_surface",
    kind: "pty",
    script: "scripts/command_argument_surface_pty.json",
    description: "Known slash-command argument validation surface",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/commandArgumentSurfaceCheck.ts",
  },
  {
    id: "deferred_command_truth",
    kind: "pty",
    script: "scripts/deferred_command_truth_pty.json",
    description: "Feature-gated parity commands render truthful disabled results and never submit to the model",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/deferredCommandTruthCheck.ts",
  },
  {
    id: "diff_transcript_entrypoint",
    kind: "pty",
    script: "scripts/diff_transcript_entrypoint_pty.json",
    description: "Exact /diff opens the real transcript diff projection or renders a truthful no-diff result",
    mockSseScript: "scripts/mock_sse_diff.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/diffTranscriptEntrypointCheck.ts",
  },
  {
    id: "permissions_status_surface",
    kind: "pty",
    script: "scripts/permissions_status_surface_pty.json",
    description: "Read-only permission status command renders launch/approval truth and never submits to the model",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "node dist/main.js repl --permission-mode prompt",
    postCheckScript: "tools/assertions/permissionsStatusSurfaceCheck.ts",
  },
  {
    id: "transcript_save",
    kind: "pty",
    script: "scripts/transcript_save_pty.json",
    description: "Transcript viewer save/export flow",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/transcriptSaveExportCheck.ts",
  },
  {
    id: "detailed_transcript",
    kind: "pty",
    script: "scripts/detailed_transcript_pty.json",
    description: "Transcript viewer detailed toggle (Ctrl+O)",
    mockSseScript: "scripts/mock_sse_tool_verbose.json",
    submitTimeoutMs: 0,
  },
  {
    id: "multiline_enter",
    kind: "pty",
    script: "scripts/multiline_enter_pty.json",
    description: "Shift+Enter/CSI-u newline handling",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "multiline_enter_legacy",
    kind: "pty",
    script: "scripts/multiline_enter_legacy_pty.json",
    description: "Legacy modified Enter sequences (CSI ~)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "permission_note",
    kind: "pty",
    script: "scripts/permission_note_pty.json",
    description: "Permission modal note input + submission",
    mockSseScript: "scripts/mock_sse_permission_note.json",
    submitTimeoutMs: 0,
  },
  {
    id: "model_picker",
    kind: "pty",
    script: "scripts/model_picker_pty.json",
    description: "Model picker open, filter, select",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: "scripts/mock_model_catalog.json",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "model_picker_provider_filter",
    kind: "pty",
    script: "scripts/model_picker_provider_filter_pty.json",
    description: "Model picker provider filter (←/→ + backspace)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: "scripts/mock_model_catalog.json",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "model_picker_page",
    kind: "pty",
    script: "scripts/model_picker_page_pty.json",
    description: "Model picker page up/down navigation",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: "scripts/mock_model_catalog.json",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "model_picker_small",
    kind: "pty",
    script: "scripts/model_picker_small_pty.json",
    description: "Model picker layout snapshot (60x18)",
    winchScript: "scripts/model_picker_small_winch.json",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: "scripts/mock_model_catalog.json",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "live_engine_smoke",
    kind: "pty",
    script: "scripts/live_engine_smoke_pty.json",
    description: "Live engine smoke (requires running CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_wrapper_plain_smoke",
    kind: "pty",
    script: "scripts/live_wrapper_plain_smoke_pty.json",
    description: "Installed bb wrapper plain-answer smoke against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperPlainSmokeCheck.ts",
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_emulator_plain_smoke",
    kind: "emulator",
    script: "scripts/live_wrapper_plain_smoke_pty.json",
    description: "Experimental WezTerm observer smoke for installed bb wrapper against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorSmokeCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 120,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_emulator_resize_smoke",
    kind: "emulator",
    script: "scripts/live_wrapper_emulator_resize_pty.json",
    description: "Experimental WezTerm observer resize smoke for installed bb wrapper against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorResizeCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 120,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_emulator_startup_smallheight",
    kind: "emulator",
    script: "scripts/live_wrapper_startup_smallheight_pty.json",
    description: "WezTerm observer constrained-height startup smoke for installed bb wrapper against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorStartupSmallHeightCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 12,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "maintenance_wrapper_plain_smoke",
    kind: "pty",
    script: "scripts/maintenance_wrapper_plain_smoke_pty.json",
    description: "Installed bb wrapper plain-answer smoke against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperPlainSmokeCheck.ts",
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_multiturn_ordering",
    kind: "pty",
    script: "scripts/maintenance_wrapper_multiturn_ordering_pty.json",
    description: "Installed bb wrapper multi-turn ordering smoke against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperMultiturnOrderingCheck.ts",
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_tool_smoke",
    kind: "pty",
    script: "scripts/maintenance_wrapper_tool_smoke_pty.json",
    description: "Installed bb wrapper tool smoke against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperToolSmokeCheck.ts",
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_resize_spacer_regression",
    kind: "pty",
    script: "scripts/maintenance_wrapper_resize_spacer_regression_pty.json",
    description: "Installed bb wrapper resize spacer regression smoke against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/noReactDuplicateKeyWarningCheck.ts",
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_compact_session_header",
    kind: "pty",
    script: "scripts/maintenance_wrapper_compact_session_header_pty.json",
    description: "Installed bb wrapper compact inline session-header regression against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperCompactSessionHeaderCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 18,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_markdown_projection",
    kind: "pty",
    script: "scripts/maintenance_wrapper_markdown_projection_pty.json",
    description: "Installed bb wrapper rich markdown projection should keep stacked table and fenced code cues visible",
    mockSseScript: "scripts/mock_sse_markdown_projection.json",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperMarkdownProjectionCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 48,
    rows: 24,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_emulator_plain_smoke",
    kind: "emulator",
    script: "scripts/maintenance_wrapper_plain_smoke_pty.json",
    description: "WezTerm observer smoke for installed bb wrapper against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorSmokeCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 120,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_emulator_resize_smoke",
    kind: "emulator",
    script: "scripts/maintenance_wrapper_emulator_resize_pty.json",
    description: "WezTerm observer resize smoke for installed bb wrapper against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/maintenanceWrapperEmulatorResizeCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 120,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "maintenance_wrapper_emulator_startup_smallheight",
    kind: "emulator",
    script: "scripts/live_wrapper_startup_smallheight_pty.json",
    description: "WezTerm observer constrained-height startup smoke for installed bb wrapper against a fresh local mock bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorStartupSmallHeightCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 12,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_wrapper_emulator_long_answer_resize",
    kind: "emulator",
    script: "scripts/live_wrapper_long_answer_resize_pty.json",
    description: "WezTerm observer long-answer resize evidence bundle for installed bb wrapper against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/liveWrapperLongAnswerResizePolicyCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "e4_real_dummy_code_agent",
    kind: "pty",
    script: "scripts/e4_real_dummy_code_agent_pty.json",
    description: "Real E4 config dummy-workspace code-agent parity session against a fresh live bridge.",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/e4RealHarnessParityCheck.ts",
    command: "bb repl",
    cwd: "/tmp/bb_tmp_harness_parity_dummy/session_e4_real",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_multiagent_repo_scan",
    kind: "pty",
    script: "scripts/live_multiagent_repo_scan_pty.json",
    description: "Live Codex GPT-5.4-mini multiagent repo-scanner taskboard smoke in a P16 isolated dummy workspace.",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveMultiagentRepoScanCheck.ts",
    command: "bb repl",
    cwd: "/tmp/bb_p16_live_multiagent_repo_scan",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live_multiagent.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    workspaceFiles: [
      {
        path: "README.md",
        contents: "# P16 Live Multiagent Repo Scan\n\nThis disposable repo is used only for BreadBoard live multiagent TUI validation.\n",
      },
      {
        path: "package.json",
        contents: "{\n  \"name\": \"bb-p16-live-multiagent-repo-scan\",\n  \"version\": \"0.0.0\",\n  \"private\": true\n}\n",
      },
      {
        path: "AGENTS.md",
        contents: "# Disposable Workspace\n\nThis directory is the complete workspace. Do not inspect parent directories. Do not edit files for the live multiagent repo-scan gate.\n",
      },
    ],
  },
  {
    id: "live_wrapper_emulator_streaming_resize_churn",
    kind: "emulator",
    script: "scripts/live_wrapper_emulator_streaming_resize_churn_pty.json",
    description: "WezTerm observer repeated streaming resize churn evidence bundle for installed bb wrapper against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    postCheckScript: "tools/assertions/liveWrapperEmulatorStreamingResizeChurnCheck.ts",
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_emulator_streaming_resize_settle_probe",
    kind: "emulator",
    script: "scripts/live_wrapper_emulator_streaming_resize_settle_probe_pty.json",
    description: "WezTerm observer internal settle probe for immediate-vs-delayed post-resize streaming visibility",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_startup_smallheight",
    kind: "pty",
    script: "scripts/live_wrapper_startup_smallheight_pty.json",
    description: "Installed bb wrapper startup landing should fall back cleanly under constrained height",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 12,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/liveWrapperStartupSmallHeightCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_multiturn_ordering",
    kind: "pty",
    script: "scripts/live_wrapper_multiturn_ordering_pty.json",
    description: "Installed bb wrapper multi-turn transcript ordering against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/liveWrapperMultiturnOrderingCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_tool_smoke",
    kind: "pty",
    script: "scripts/live_wrapper_tool_pty.json",
    description: "Installed bb wrapper tool-turn inspection against a fresh live bridge",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/liveWrapperToolSmokeCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_resize_spacer_regression",
    kind: "pty",
    script: "scripts/live_wrapper_resize_spacer_regression_pty.json",
    description: "Installed bb wrapper resize churn should not emit duplicate spacer-key warnings",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/noReactDuplicateKeyWarningCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_wrapper_long_answer_resize",
    kind: "pty",
    script: "scripts/live_wrapper_long_answer_resize_pty.json",
    description: "Installed bb wrapper long streamed answer with resize for managed-region reclaim audit",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/liveWrapperLongAnswerResizePolicyCheck.ts",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "live_engine_permission",
    kind: "pty",
    script: "scripts/live_engine_permission_pty.json",
    description: "Live engine permission modal flow (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    command: "node dist/main.js repl --permission-mode prompt",
    env: {
      BREADBOARD_DEBUG_PERMISSIONS: "1",
    },
  },
  {
    id: "live_engine_model_picker",
    kind: "pty",
    script: "scripts/live_engine_model_picker_pty.json",
    description: "Live engine model picker (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_stop_retry",
    kind: "pty",
    script: "scripts/live_engine_stop_retry_pty.json",
    description: "Live engine stop + /retry semantics (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_tool_events",
    kind: "pty",
    script: "scripts/live_engine_tool_events_pty.json",
    description: "Live engine tool events (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_rewind",
    kind: "pty",
    script: "scripts/live_engine_rewind_pty.json",
    description: "Live engine rewind checkpoints (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_skills",
    kind: "pty",
    script: "scripts/live_engine_skills_pty.json",
    description: "Live engine skills picker (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_ctree",
    kind: "pty",
    script: "scripts/live_engine_ctree_pty.json",
    description: "Live engine CTree summary (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
  },
  {
    id: "keymap_codex",
    kind: "pty",
    script: "scripts/keymap_codex_pty.json",
    description: "Codex keymap (Ctrl+T transcript)",
    mockSseScript: "scripts/mock_sse_sample.json",
    env: {
      BREADBOARD_TUI_KEYMAP: "codex",
    },
  },
  {
    id: "opentui_slab_smoke",
    kind: "pty",
    script: "scripts/opentui_slab_smoke_pty.json",
    description: "OpenTUI splitHeight slab smoke (scrollback-first transcript)",
    command: "bun run index.ts",
    cwd: "../opentui_slab",
    submitTimeoutMs: 0,
  },
  {
    id: "opentui_slab_resize_storm",
    kind: "pty",
    script: "scripts/opentui_slab_resize_storm_pty.json",
    description: "OpenTUI splitHeight slab under resize storm",
    command: "bun run index.ts",
    cwd: "../opentui_slab",
    winchScript: "scripts/opentui_slab_resize_storm_winch.json",
    submitTimeoutMs: 0,
  },
  {
    id: "opentui_slab_paste_torture",
    kind: "pty",
    script: "scripts/opentui_slab_paste_torture_pty.json",
    description: "OpenTUI splitHeight slab bracketed paste torture",
    command: "bun run index.ts",
    cwd: "../opentui_slab",
    submitTimeoutMs: 0,
  },
  {
    id: "paste_flood",
    kind: "repl",
    script: "scripts/paste_flood.json",
    description: "Large bracketed paste handling",
    mockSseScript: "scripts/mock_sse_diff.json",
  },
  {
    id: "token_flood",
    kind: "repl",
    script: "scripts/token_flood.json",
    description: "Streaming flood / guardrail state",
    mockSseScript: "scripts/mock_sse_guardrail.json",
  },
  {
    id: "ctrl_v_paste",
    kind: "pty",
    script: "scripts/ctrl_v_paste.json",
    clipboardText: "Stress runner clipboard payload",
    description: "Ctrl+V text paste w/ chips",
    mockSseScript: "scripts/mock_sse_sample.json",
  },
  {
    id: "ctrl_v_paste_large",
    kind: "pty",
    script: "scripts/ctrl_v_paste.json",
    clipboardText: LARGE_CLIPBOARD_PAYLOAD,
    description: "Ctrl+V large text paste, expect placeholder chip",
    clipboardAssertions: { expectTextChip: true },
    mockSseScript: "scripts/mock_sse_sample.json",
  },
  {
    id: "paste_undo",
    kind: "pty",
    script: "scripts/paste_undo_pty.json",
    clipboardText: LARGE_CLIPBOARD_PAYLOAD,
    description: "Paste chip then undo (Ctrl+Z) removes chip",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "paste_undo_redo",
    kind: "pty",
    script: "scripts/paste_undo_redo_pty.json",
    clipboardText: LARGE_CLIPBOARD_PAYLOAD,
    description: "Paste chip then undo/redo (Ctrl+Z/Ctrl+Y)",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
  },
  {
    id: "p4_terminal_adapter_dry_run",
    kind: "terminal",
    terminalLane: "dry-run",
    script: "scripts/composer_command_surface_pty.json",
    description: "P4 terminal-adapter integration dry-run through the canonical bundle runner",
    submitTimeoutMs: 0,
  },
  {
    id: "p14_wezterm_command_surface_real_terminal",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/composer_command_surface_pty.json",
    description: "P14 WezTerm real-terminal slash command/composer command surface support lane.",
    submitTimeoutMs: 0,
    command: "bb repl",
    configPath: "../agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 110,
    rows: 34,
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/composerCommandSurfaceCheck.ts",
  },
  {
    id: "p15_wezterm_core_ux_transcript_navigation",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/transcript_viewer_navigation_pty.json",
    description: "P15 WezTerm real-terminal transcript viewer navigation/search/return support lane.",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 120,
    rows: 34,
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/transcriptViewerNavigationCheck.ts",
  },
  {
    id: "p16_wezterm_multiagent_task_focus",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/multiagent_task_focus_controls_wezterm.json",
    description:
      "P16 WezTerm representative multiagent host lane: taskboard filters, Task Focus inspector semantics, artifact tail, follow toggle, compact resize, and close recovery.",
    mockSseScript: "scripts/mock_sse_multiagent_tasks.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    workspaceFiles: [
      {
        path: "agent_ws/.breadboard/subagents/agent-task-implementer-01.jsonl",
        contents: [
          "{\"event\":\"start\",\"message\":\"SMTP focus artifact opened\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_01 create socket\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_02 bind localhost\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_03 listen\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_04 accept client\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_05 parse HELO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_06 parse MAIL FROM\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_07 parse RCPT TO\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_08 parse DATA\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_09 persist eml\"}",
          "{\"event\":\"write\",\"path\":\"smtp_server.c\",\"message\":\"SMTP_FOCUS_TAIL_LINE_10 graceful QUIT\"}",
          "{\"event\":\"summary\",\"message\":\"SMTP_FOCUS_TAIL_LINE_11 deterministic tail marker\"}",
          "",
        ].join("\n"),
      },
    ],
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_SUBAGENTS_V2_ENABLED: "1",
      BREADBOARD_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_SUBAGENTS_COALESCE_MS: "0",
      BREADBOARD_TUI_SUBAGENTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_STRIP_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TOASTS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_TASKBOARD_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_FOCUS_ENABLED: "1",
      BREADBOARD_TUI_SUBAGENTS_COALESCE_MS: "0",
    },
    postCheckScript: "tools/assertions/multiagentTaskFocusControlsCheck.ts",
  },
  {
    id: "p15_wezterm_core_ux_model_picker",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p14_model_picker_ghostty_real_terminal_pty.json",
    description: "P15 WezTerm real-terminal model picker open/filter/resize/close support lane.",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: resolveTuiPath("scripts/mock_model_catalog.json"),
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "p15_wezterm_core_ux_permissions_status",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/permissions_status_surface_pty.json",
    description: "P15 WezTerm real-terminal read-only permission status surface support lane.",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bb repl --permission-mode prompt",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 120,
    rows: 34,
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/permissionsStatusSurfaceCheck.ts",
  },
  {
    id: "p15_wezterm_core_ux_diff_transcript_entrypoint",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/diff_transcript_entrypoint_pty.json",
    description: "P15 WezTerm real-terminal /diff transcript entrypoint support lane.",
    mockSseScript: "scripts/mock_sse_diff.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/diffTranscriptEntrypointCheck.ts",
  },
  {
    id: "p4_wezterm_streaming_smoke",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/streaming_markdown_pty.json",
    description: "P4 WezTerm programmable-lane streaming smoke against the canonical app with deterministic mock SSE",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
  },
  {
    id: "p4_wezterm_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_multiturn_width_shrink_pty.json",
    description: "P4 WezTerm programmable-lane multi-turn width-shrink probe against the live bridge mock provider",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "p4_ghostty_launch_stream_probe",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_visual_probe_pty.json",
    description: "P4 Ghostty semi-automated launch and streaming visual probe with structured screenshots and logs",
    submitTimeoutMs: 0,
    command: String.raw`bash -lc 'printf "ghostty visual probe start\n"; for i in 1 2 3 4; do printf "ghostty stream chunk %s\n" "$i"; sleep 1; done; sleep 20'`,
    cwd: "..",
    cols: 132,
    rows: 36,
  },
  {
    id: "p4_ghostty_streaming_current_window",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_streaming_current_window_pty.json",
    description: "P4 Ghostty mirrored startup/streaming current-window probe using a tmux-driven BreadBoard session rendered inside a real Ghostty window",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p4ghostty_stream:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p4ghostty_stream",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
    },
  },
  {
    id: "p4_ghostty_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_multiturn_width_shrink_pty.json",
    description: "P4 Ghostty mirrored multi-turn width-shrink probe using a tmux-driven BreadBoard session rendered inside a real Ghostty window",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p4ghostty_resize:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p4ghostty_resize",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      P4_GHOSTTY_SECOND_DELAY_MS: "7000",
    },
  },
  {
    id: "p5_wezterm_owned_live_streaming_smoke",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/streaming_markdown_pty.json",
    description: "P5 owned-live WezTerm streaming smoke against the canonical app with deterministic mock SSE",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
  },
  {
    id: "p5_wezterm_owned_live_transcript_navigation",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/transcript_viewer_navigation_pty.json",
    description: "P5 owned-live WezTerm transcript escape and return probe",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
    postCheckScript: "tools/assertions/transcriptViewerNavigationCheck.ts",
  },
  {
    id: "p5_wezterm_owned_live_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p4_wezterm_multiturn_width_shrink_pty.json",
    description: "P5 owned-live WezTerm multi-turn width-shrink probe against the live bridge mock provider",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
  },
  {
    id: "p5_wezterm_owned_live_height_change",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p5_wezterm_owned_live_height_change_pty.json",
    description: "P5 owned-live WezTerm active height-change probe against the deterministic streaming mock",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow_two_turns.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
    postCheckScript: "tools/assertions/ownedLiveHeightChangeCheck.ts",
  },
  {
    id: "p5_wezterm_owned_live_network_error",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/network_error_pty.json",
    description: "P5 owned-live WezTerm network-error surface probe against the deterministic mock",
    mockSseScript: "scripts/mock_sse_network_error.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
  },
  {
    id: "p6_wezterm_escalated_streaming_smoke",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_streaming_smoke_pty.json",
    description: "P6 escalated-owned WezTerm streaming smoke against the canonical app with deterministic mock SSE",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
    },
  },
  {
    id: "p6_wezterm_escalated_transcript_navigation",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_transcript_escape_pty.json",
    description: "P6 escalated-owned WezTerm transcript escape and return probe",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
    },
    postCheckScript: "tools/assertions/escalatedTranscriptEscapeCheck.ts",
  },
  {
    id: "p6_wezterm_escalated_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_multiturn_width_shrink_pty.json",
    description: "P6 escalated-owned WezTerm multi-turn width-shrink probe against the live bridge mock provider",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
    },
  },
  {
    id: "p6_wezterm_escalated_network_error",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_network_error_pty.json",
    description: "P6 escalated-owned WezTerm network-error surface probe against the deterministic mock",
    mockSseScript: "scripts/mock_sse_network_error.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_STREAM_MAX_RETRIES: "1",
      BREADBOARD_STREAM_STALL_TIMEOUT_MS: "3000",
    },
    postCheckScript: "tools/assertions/ownedLiveNetworkErrorCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_streaming_smoke",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_streaming_smoke_pty.json",
    description: "P7 scene-owned runtime WezTerm streaming smoke against the canonical app with deterministic mock SSE",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeStreamingCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_transcript_navigation",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_transcript_escape_pty.json",
    description: "P7 scene-owned runtime WezTerm transcript escape and return probe",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeTranscriptEscapeCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_multiturn_width_shrink_pty.json",
    description: "P7 scene-owned runtime WezTerm multi-turn width-shrink probe against the live bridge mock provider",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeWidthShrinkCheck.ts",
  },
  {
    id: "p7_ghostty_scene_owned_streaming_current_window",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_streaming_current_window_pty.json",
    description: "P7 scene-owned runtime Ghostty mirrored startup/streaming current-window probe using the real Ghostty lane",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_stream:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_stream",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyStreamingCurrentWindowCheck.ts",
  },
  {
    id: "p7_wezterm_default_flagship_smoke",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_streaming_smoke_pty.json",
    description: "Post-closeout ceremonial WezTerm flagship smoke using the default resolver path without ownership env overrides",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    postCheckScript: "tools/assertions/sceneOwnedRuntimeStreamingCheck.ts",
  },
  {
    id: "p7_ghostty_default_flagship_smoke",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_streaming_current_window_pty.json",
    description: "Post-closeout ceremonial Ghostty flagship smoke using the default resolver path without ownership env overrides",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_default_stream:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_default_stream",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyStreamingCurrentWindowCheck.ts",
  },
  {
    id: "p8_wezterm_scrollback_seeded_host_history_guard",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_streaming_smoke_pty.json",
    description: "P8 preserved-scrollback WezTerm seeded-host-history guard using canonical app startup plus pre-app shell lines.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\nPRE_APP_BETA\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
    },
    postCheckScript: "tools/assertions/scrollbackHostHistoryPreservationCheck.ts",
  },
  {
    id: "p8_wezterm_scrollback_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_multiturn_width_shrink_pty.json",
    description: "P8 preserved-scrollback WezTerm visible-region width-shrink probe for landing/history precision.",
    requiresLive: true,
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
    },
    postCheckScript: "tools/assertions/scrollbackVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p10_ghostty_native_scrollback_seeded_host_history_guard",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p10_ghostty_native_scrollback_seeded_host_history_pty.json",
    description: "P10 Ghostty-native no-tmux seeded-host-history guard using Ghostty write_scrollback_file export.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyHostHistoryPreservationCheck.ts",
  },
  {
    id: "p10_ghostty_native_scrollback_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p10_ghostty_native_scrollback_width_shrink_pty.json",
    description: "P10 Ghostty-native no-tmux single-turn visible-region width-shrink probe using Ghostty write_screen_file/write_scrollback_file exports.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p10_ghostty_native_scrollback_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p10_ghostty_native_scrollback_multiturn_width_shrink_pty.json",
    description: "P10 Ghostty-native no-tmux two-turn preserved-scrollback width-shrink probe using Ghostty write_screen_file/write_scrollback_file exports.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow_two_turns.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p10_ghostty_native_scrollback_height_change",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p10_ghostty_native_scrollback_height_change_pty.json",
    description: "P10 Ghostty-native no-tmux preserved-scrollback height-shrink probe using native screen/scrollback exports.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p10_ghostty_native_scrollback_resize_churn",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p10_ghostty_native_scrollback_resize_churn_pty.json",
    description: "P10 Ghostty-native no-tmux preserved-scrollback repeated resize-churn probe with final shrink verification.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_history_guard_v2",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_history_guard_v2_pty.json",
    description: "P11 Ghostty-native rich seeded-host-history guard with P11 clause-complete verdicts.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\nBB_PRE_SHORT_ALPHA\\nBB_PRE_SHORT_BETA\\nBB_PRE_LONG_GAMMA_BEGIN abcdefghijklmnopqrstuvwxyz abcdefghijklmnopqrstuvwxyz abcdefghijklmnopqrstuvwxyz BB_PRE_LONG_GAMMA_END\\nBB_PRE_FINAL_ZETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyHostHistoryPreservationCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_width_equiv_structured_wide",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_width_equiv_structured_wide_pty.json",
    description: "P11 Ghostty-native width-equivalence Path A: wide start then shrink for structured markdown.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_width_equiv_structured_narrow",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_width_equiv_structured_narrow_pty.json",
    description: "P11 Ghostty-native width-equivalence Path B: resize to final width before structured markdown input.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/visibleCompositionCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_multiturn_width_shrink_pty.json",
    description: "P11 Ghostty-native two-turn preserved-scrollack width-shrink case with P11 clause-complete verdicts.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow_two_turns.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_height_change",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_height_change_pty.json",
    description: "P11 Ghostty-native height-change preserved-scrollback case with P11 clause-complete verdicts.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p11_ghostty_native_scrollback_resize_churn",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p11_ghostty_native_scrollback_resize_churn_pty.json",
    description: "P11 Ghostty-native resize-churn preserved-scrollback case with P11 clause-complete verdicts.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p14_ghostty_model_picker_real_terminal",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p14_model_picker_ghostty_real_terminal_pty.json",
    description: "P14 Ghostty real-terminal model picker open/filter/resize/close support lane.",
    command: "bash -lc 'printf \"PRE_APP_ALPHA\\nPRE_APP_BETA\\n\"; exec bb repl \"$@\"' bash",
    configPath: "../agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cwd: QC_DUMMY_WORKSPACE_CWD,
    cols: 132,
    rows: 36,
    submitTimeoutMs: 0,
    env: {
      BREADBOARD_MODEL_CATALOG_PATH: resolveTuiPath("scripts/mock_model_catalog.json"),
      BREADBOARD_PTY_PRESERVE_HOME: "1",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
    },
    postCheckScript: "tools/assertions/overlayFootprintCheck.ts",
  },
  {
    id: "p15_ghostty_core_ux_transcript_navigation",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/transcript_viewer_navigation_ghostty_pty.json",
    description: "P15 Ghostty real-terminal transcript viewer navigation/search/return support lane.",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cols: 120,
    rows: 34,
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p15ghostty_core_transcript:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p15ghostty_core_transcript",
      P4_GHOSTTY_PRE_APP_TEXT: "PRE_APP_ALPHA\nPRE_APP_BETA\n",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/transcriptViewerNavigationCheck.ts",
  },
  {
    id: "p15_ghostty_core_ux_permissions_status",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/permissions_status_surface_ghostty_pty.json",
    description: "P15 Ghostty real-terminal read-only permission status surface support lane.",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cols: 120,
    rows: 34,
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p15ghostty_core_permissions:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p15ghostty_core_permissions",
      P4_GHOSTTY_PRE_APP_TEXT: "PRE_APP_ALPHA\nPRE_APP_BETA\n",
      P4_GHOSTTY_BOOT_DELAY_MS: "6000",
      P4_GHOSTTY_PERMISSION_MODE: "prompt",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/permissionsStatusSurfaceCheck.ts",
  },
  {
    id: "p15_ghostty_core_ux_diff_transcript_entrypoint",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/diff_transcript_entrypoint_ghostty_pty.json",
    description: "P15 Ghostty real-terminal /diff transcript entrypoint support lane.",
    mockSseScript: "scripts/mock_sse_diff.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    cols: 132,
    rows: 36,
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p15ghostty_core_diff:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p15ghostty_core_diff",
      P4_GHOSTTY_PRE_APP_TEXT: "PRE_APP_ALPHA\nPRE_APP_BETA\n",
      P4_GHOSTTY_BOOT_DELAY_MS: "6000",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/diffTranscriptEntrypointCheck.ts",
  },
  {
    id: "p8_ghostty_scrollback_seeded_host_history_guard",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_streaming_current_window_pty.json",
    description: "P8 preserved-scrollback Ghostty seeded-host-history guard using canonical app startup plus pre-app shell lines.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p8ghostty_seed:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p8ghostty_seed",
      P4_GHOSTTY_PRE_APP_TEXT: "PRE_APP_ALPHA\nPRE_APP_BETA\n",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "6500",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyHostHistoryPreservationCheck.ts",
  },
  {
    id: "p8_ghostty_scrollback_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p8_ghostty_scrollback_width_shrink_pty.json",
    description: "P8 preserved-scrollback Ghostty mirrored visible-region width-shrink probe.",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_QC_WORKSPACE_CWD: QC_GHOSTTY_TMUX_WORKSPACE_CWD,
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p8ghostty_resize:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p8ghostty_resize",
      P4_GHOSTTY_PRE_APP_TEXT: "PRE_APP_ALPHA\nPRE_APP_BETA\n",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "12000",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "inline-scrollback",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/scrollbackGhosttyVisibleRegionBlastRadiusCheck.ts",
  },
  {
    id: "p7_ghostty_scene_owned_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_multiturn_width_shrink_pty.json",
    description: "P7 scene-owned runtime Ghostty mirrored multi-turn width-shrink probe against the live bridge mock provider",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_resize:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_resize",
      P4_GHOSTTY_PROMPT: "Write a medium markdown answer with a heading, bullets, a small table, and a code block so the current window has visible structured content.",
      P4_GHOSTTY_SECOND_PROMPT: "Give a short follow-up paragraph and a two-item bullet list.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      P4_GHOSTTY_SECOND_DELAY_MS: "7000",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyWidthShrinkCheck.ts",
  },
  {
    id: "p7_ghostty_scene_owned_height_change",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_height_change_pty.json",
    description: "P7 scene-owned runtime Ghostty mirrored active height-change credibility probe",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_height:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_height",
      P4_GHOSTTY_PROMPT: "stream markdown",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyHeightChangeCheck.ts",
  },
  {
    id: "p7_ghostty_scene_owned_network_error",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_network_error_pty.json",
    description: "P7 scene-owned runtime Ghostty mirrored network-error credibility probe",
    mockSseScript: "scripts/mock_sse_network_error.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_neterr:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_neterr",
      P4_GHOSTTY_PROMPT: "trigger network error",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
      BREADBOARD_STREAM_MAX_RETRIES: "1",
      BREADBOARD_STREAM_STALL_TIMEOUT_MS: "3000",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyNetworkErrorCheck.ts",
  },
  {
    id: "p7_ghostty_scene_owned_streaming_resize_churn",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p7_ghostty_scene_owned_streaming_resize_churn_pty.json",
    description: "P7 scene-owned runtime Ghostty mirrored repeated streaming resize-churn credibility probe",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p7ghostty_churn:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p7ghostty_churn",
      P4_GHOSTTY_PROMPT: "Answer with a short markdown response containing a heading, three bullets, a small table, and a ts code block.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_GHOSTTY_NATIVE_SCROLLBACK_EXPORT: "1",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedGhosttyResizeChurnCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_height_change",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p5_wezterm_owned_live_height_change_pty.json",
    description: "P7 scene-owned runtime WezTerm active height-change probe against the deterministic streaming mock",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeHeightChangeCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_network_error",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/p6_wezterm_escalated_network_error_pty.json",
    description: "P7 scene-owned runtime WezTerm network-error surface probe against the deterministic mock",
    mockSseScript: "scripts/mock_sse_network_error.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
      BREADBOARD_STREAM_MAX_RETRIES: "1",
      BREADBOARD_STREAM_STALL_TIMEOUT_MS: "3000",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeNetworkErrorCheck.ts",
  },
  {
    id: "p7_wezterm_scene_owned_streaming_resize_churn",
    kind: "terminal",
    terminalLane: "wezterm",
    script: "scripts/live_wrapper_emulator_streaming_resize_churn_pty.json",
    description: "P7 scene-owned runtime WezTerm repeated streaming resize churn probe",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bb repl",
    cwd: "..",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_TUI_SCENE_OWNED_STRATEGY: "scene-owned-runtime",
    },
    postCheckScript: "tools/assertions/sceneOwnedRuntimeResizeChurnCheck.ts",
  },
  {
    id: "p5_ghostty_owned_live_streaming_current_window",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_streaming_current_window_pty.json",
    description: "P5 owned-live Ghostty mirrored startup/streaming current-window probe using the real Ghostty lane",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p5ghostty_stream:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p5ghostty_stream",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
    },
  },
  {
    id: "p5_ghostty_owned_live_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_multiturn_width_shrink_pty.json",
    description: "P5 owned-live Ghostty mirrored multi-turn width-shrink probe against the live bridge mock provider",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p5ghostty_resize:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p5ghostty_resize",
      P4_GHOSTTY_PROMPT: "Write a medium markdown answer with a heading, bullets, a small table, and a code block so the current window has visible structured content.",
      P4_GHOSTTY_SECOND_PROMPT: "Give a short follow-up paragraph and a two-item bullet list.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      P4_GHOSTTY_SECOND_DELAY_MS: "7000",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "p6_ghostty_escalated_streaming_current_window",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_streaming_current_window_pty.json",
    description: "P6 escalated-owned Ghostty mirrored startup/streaming current-window probe using the real Ghostty lane",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta_slow.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/codex_cli_gpt54mini_e4_live.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p6ghostty_stream:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p6ghostty_stream",
      P4_GHOSTTY_PROMPT: "Write a markdown answer with a heading, three bullets, a small table, and a ts code block. Keep it medium length.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
    },
  },
  {
    id: "p6_ghostty_escalated_multiturn_width_shrink",
    kind: "terminal",
    terminalLane: "ghostty",
    script: "scripts/p4_ghostty_multiturn_width_shrink_pty.json",
    description: "P6 escalated-owned Ghostty mirrored multi-turn width-shrink probe against the live bridge mock provider",
    mockSseScript: "scripts/mock_sse_streaming_markdown_delta.json",
    submitTimeoutMs: 0,
    command: "bash scripts/harness/ghosttyTmuxSession.sh",
    cols: 132,
    rows: 36,
    configPath: "agent_configs/misc/opencode_mock_c_fs.yaml",
    env: {
      BREADBOARD_GHOSTTY_TMUX_TARGET: "p6ghostty_resize:0.0",
      P4_GHOSTTY_TMUX_SESSION: "p6ghostty_resize",
      P4_GHOSTTY_PROMPT: "Write a medium markdown answer with a heading, bullets, a small table, and a code block so the current window has visible structured content.",
      P4_GHOSTTY_SECOND_PROMPT: "Give a short follow-up paragraph and a two-item bullet list.",
      P4_GHOSTTY_BOOT_DELAY_MS: "3200",
      P4_GHOSTTY_FIRST_DELAY_MS: "2200",
      P4_GHOSTTY_SECOND_DELAY_MS: "7000",
      BREADBOARD_TUI_LIVE_SHELL_MODE: "owned-live",
      BREADBOARD_TUI_LIVE_SHELL_HOST: "escalated-owned",
      BREADBOARD_PTY_PRESERVE_HOME: "1",
    },
  },
  {
    id: "attachment_submit",
    kind: "pty",
    script: "scripts/attachment_submit.json",
    clipboardText:
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9YpDkdIAAAAASUVORK5CYII=",
    description: "Attachment capture + submission",
    mockSseScript: "scripts/mock_sse_sample.json",
    clipboardAssertions: {
      expectAttachmentChip: true,
      expectImageAttachment: true,
      expectedImageMetadata: { bytes: 68 },
    },
  },
]

interface CliOptions {
  cases: ReadonlyArray<string> | null
  configPath: string
  liveConfigPath: string | null
  baseUrl: string
  outDir: string
  command: string
  zip: boolean
  guardLogs: ReadonlyArray<string>
  skipGuardMetrics: boolean
  mockSseConfig: MockSseOptions | null
  mockSseDefaults: Omit<MockSseOptions, "script">
  useCaseMockSse: boolean
  includeLive: boolean
  autoStartLive: boolean
  freshLive: boolean
  bundleKind: string
  bundleAuthority: "canonical" | "maintenance" | "observer" | "emulator" | "legacy"
  allowRevisionMismatch: boolean
  tmuxCaptureTarget: string | null
  tmuxCaptureScale: number
  keyFuzzIterations: number
  keyFuzzSteps: number
  keyFuzzSeed: number | null
  bridgeChaos: Record<string, unknown> | null
  chaosInfo: Record<string, unknown> | null
  contractOverrides: ContractOptions | null
}

interface MockSseOptions {
  readonly script: string
  readonly host: string
  readonly port: number
  readonly loop: boolean
  readonly delayMultiplier: number
  readonly jitterMs: number
  readonly dropRate: number
}

const parseCliArgs = (): CliOptions => {
  const args = process.argv.slice(2)
  const selected: string[] = []
  let configPath = DEFAULT_CONFIG
  let liveConfigPath: string | null = DEFAULT_LIVE_CONFIG
  let baseUrl = DEFAULT_BASE_URL
  let outDir = path.join(ROOT_DIR, "artifacts", "stress")
  let command = DEFAULT_COMMAND
  let zip = true
  const guardLogs: string[] = []
  let skipGuardMetrics = false
  let mockSseScript: string | null = null
  let mockSseHost = "127.0.0.1"
  let mockSsePort = 9191
  let mockSseLoop = false
  let mockSseDelayMultiplier = 1
  let mockSseJitterMs = 0
  let mockSseDropRate = 0
  let useCaseMockSse = true
  let includeLive = process.env.STRESS_INCLUDE_LIVE === "1" || process.env.BREADBOARD_STRESS_INCLUDE_LIVE === "1"
  let autoStartLive = process.env.BREADBOARD_CLI_AUTO_START === "1"
  let freshLive = process.env.BREADBOARD_STRESS_FRESH_LIVE === "1"
  let bundleKind = process.env.BREADBOARD_BUNDLE_KIND ?? "ad-hoc"
  let bundleAuthority = (process.env.BREADBOARD_BUNDLE_AUTHORITY as
    | "canonical"
    | "maintenance"
    | "observer"
    | "emulator"
    | "legacy"
    | undefined) ?? "canonical"
  let allowRevisionMismatch = process.env.BREADBOARD_ALLOW_REVISION_MISMATCH === "1"
  let tmuxCaptureTarget: string | null = process.env.BREADBOARD_TMUX_CAPTURE_TARGET ?? null
  let tmuxCaptureScale = Number(process.env.BREADBOARD_TMUX_CAPTURE_SCALE ?? "1")
  let keyFuzzIterations = 0
  let keyFuzzSteps = 60
  let keyFuzzSeed: number | null = null
  let legacyContract = false
  const bridgeChaos = readBridgeChaosFromEnv()

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i]
    switch (arg) {
      case "--case":
        selected.push(args[++i])
        break
      case "--config":
        configPath = args[++i]
        break
      case "--live-config":
        liveConfigPath = args[++i]
        break
      case "--base-url":
        baseUrl = args[++i]
        break
      case "--out-dir":
        outDir = args[++i]
        break
      case "--cmd":
        command = args[++i]
        break
      case "--no-zip":
        zip = false
        break
      case "--guard-log":
        guardLogs.push(args[++i])
        break
      case "--no-guard-metrics":
        skipGuardMetrics = true
        break
      case "--mock-sse-script":
        mockSseScript = args[++i]
        break
      case "--mock-sse-host":
        mockSseHost = args[++i]
        break
      case "--mock-sse-port":
        mockSsePort = Number(args[++i])
        break
      case "--mock-sse-loop":
        mockSseLoop = true
        break
      case "--mock-sse-delay-multiplier":
        mockSseDelayMultiplier = Number(args[++i])
        break
      case "--mock-sse-jitter-ms":
        mockSseJitterMs = Number(args[++i])
        break
      case "--mock-sse-drop-rate":
        mockSseDropRate = Number(args[++i])
        break
      case "--case-mock-sse":
        useCaseMockSse = true
        break
      case "--no-case-mock-sse":
        useCaseMockSse = false
        break
      case "--include-live":
        includeLive = true
        break
      case "--auto-start-live":
        autoStartLive = true
        break
      case "--fresh-live":
        freshLive = true
        break
      case "--warm-live":
        freshLive = false
        break
      case "--bundle-kind":
        bundleKind = args[++i]
        break
      case "--bundle-authority": {
        const value = args[++i]
        if (value === "canonical" || value === "maintenance" || value === "observer" || value === "emulator" || value === "legacy") {
          bundleAuthority = value
        }
        break
      }
      case "--allow-revision-mismatch":
        allowRevisionMismatch = true
        break
      case "--tmux-capture-target":
        tmuxCaptureTarget = args[++i] ?? null
        break
      case "--tmux-capture-scale":
        tmuxCaptureScale = Number(args[++i])
        break
      case "--key-fuzz-iterations":
        keyFuzzIterations = Number(args[++i])
        break
      case "--key-fuzz-steps":
        keyFuzzSteps = Number(args[++i])
        break
      case "--key-fuzz-seed":
        keyFuzzSeed = Number(args[++i]) >>> 0
        break
      case "--legacy-contract":
        legacyContract = true
        break
      default:
        break
    }
  }

  const absoluteOut = resolveTuiPath(outDir)
  const normalizedConfigPath = resolveRepoPath(configPath)
  const normalizedLiveConfigPath = liveConfigPath ? resolveRepoPath(liveConfigPath) : null
  const mockDefaults: Omit<MockSseOptions, "script"> = {
    host: mockSseHost,
    port: mockSsePort,
    loop: mockSseLoop,
    delayMultiplier: mockSseDelayMultiplier,
    jitterMs: mockSseJitterMs,
    dropRate: mockSseDropRate,
  }
  const mockSseConfig = mockSseScript
    ? {
        script: resolveRepoPath(mockSseScript),
        ...mockDefaults,
      }
    : null

  return {
    cases: selected.length > 0 ? selected : null,
    configPath: normalizedConfigPath,
    liveConfigPath: normalizedLiveConfigPath,
    baseUrl,
    outDir: absoluteOut,
    command,
    zip,
    guardLogs,
    skipGuardMetrics,
    mockSseConfig,
    mockSseDefaults: mockDefaults,
    useCaseMockSse,
    includeLive,
    autoStartLive,
    freshLive,
    bundleKind,
    bundleAuthority,
    allowRevisionMismatch,
    tmuxCaptureTarget,
    tmuxCaptureScale: Number.isFinite(tmuxCaptureScale) ? tmuxCaptureScale : 1,
    keyFuzzIterations,
    keyFuzzSteps,
    keyFuzzSeed,
    bridgeChaos,
    chaosInfo: mockSseConfig
      ? {
          mode: "mock-sse",
          port: mockSsePort,
          script: mockSseScript,
          delayMultiplier: mockSseDelayMultiplier,
          jitterMs: mockSseJitterMs,
          dropRate: mockSseDropRate,
        }
      : null,
    contractOverrides: legacyContract ? { requireSeq: false } : null,
  }
}

const ensureDistBuild = async () => {
  const target = path.join(ROOT_DIR, "dist", "main.js")
  try {
    await fs.access(target)
  } catch {
    throw new Error("dist/main.js not found; run `npm run build` before bundling stress artifacts.")
  }
}

const computeScriptHash = async (absolutePath: string) => {
  const buffer = await fs.readFile(absolutePath)
  return createHash("sha256").update(buffer).digest("hex")
}

const shellQuote = (value: string) => `'${value.replace(/'/g, `'\\''`)}'`

const toRepoRootPath = (value: string) => (path.isAbsolute(value) ? path.relative(ROOT_DIR, value) : value)

const formatReproCommand = (cwd: string, env: Record<string, string>, argv: string[]) => {
  const envPrefix =
    Object.keys(env).length > 0
      ? `${Object.entries(env)
          .map(([key, value]) => `${key}=${shellQuote(value)}`)
          .join(" ")} `
      : ""
  return `cd ${shellQuote(cwd)} && ${envPrefix}${argv.map((part) => shellQuote(part)).join(" ")}`
}

const buildReproInfo = (
  options: { cwd: string; env: Record<string, string>; argv: string[] },
  extra?: { prelude?: Array<{ cwd: string; env: Record<string, string>; argv: string[]; label?: string }>; notes?: string[] },
) => {
  const prelude =
    extra?.prelude?.map((entry) => ({
      label: entry.label ?? "prelude",
      cwd: entry.cwd,
      env: entry.env,
      argv: entry.argv,
      copyPaste: formatReproCommand(entry.cwd, entry.env, entry.argv),
    })) ?? []
  return {
    cwd: options.cwd,
    env: options.env,
    argv: options.argv,
    copyPaste: formatReproCommand(options.cwd, options.env, options.argv),
    prelude,
    notes: extra?.notes ?? [],
  }
}

interface MockSseHandle {
  stop: () => Promise<void>
}

const startMockSseServer = async (config: MockSseOptions): Promise<MockSseHandle> => {
  const handle = await startReplayServer({
    scriptPath: config.script,
    host: config.host,
    port: config.port,
    loop: config.loop,
    delayMultiplier: config.delayMultiplier,
    jitterMs: config.jitterMs,
    dropRate: config.dropRate,
    log: (line) => console.log(line),
  })
  return {
    stop: () => handle.close(),
  }
}

const formatTimestamp = () => {
  const now = new Date()
  const pad = (value: number) => value.toString().padStart(2, "0")
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

interface SpawnHooks {
  readonly onStdoutChunk?: (chunk: string) => void
  readonly onClose?: () => void
}

const spawnLoggedProcess = (
  command: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv },
  logPath: string,
  hooks?: SpawnHooks,
) =>
  new Promise<void>((resolve, reject) => {
    const child = spawn(command, args, options)
    const logStream = createWriteStream(logPath)
    child.stdout.on("data", (chunk: Buffer) => {
      logStream.write(chunk)
      hooks?.onStdoutChunk?.(chunk.toString("utf8"))
    })
    child.stderr.on("data", (chunk: Buffer) => {
      logStream.write(chunk)
    })
    const finalize = (code: number | null, error?: Error) => {
      hooks?.onClose?.()
      logStream.end(() => {
        if (error) {
          reject(error)
          return
        }
        if (code === 0) {
          resolve()
        } else {
          reject(new Error(`${command} exited with code ${code}. See ${logPath}`))
        }
      })
    }
    child.on("close", (code) => finalize(code))
    child.on("error", (error) => finalize(null, error))
  })

interface SseHandle {
  readonly promise: Promise<void>
  readonly stop: () => void
  readonly hasData: () => boolean
}

const writeFallbackMessage = async (targetPath: string, message: string) => {
  await fs.writeFile(targetPath, `${message}\n`, "utf8").catch(() => undefined)
}

const tapSessionEvents = (
  baseUrl: string,
  sessionId: string,
  targetPath: string,
  options?: { replay?: boolean; limit?: number; fromId?: string },
): SseHandle => {
  const controller = new AbortController()
  const stream = createWriteStream(targetPath)
  let didWrite = false
  let aborted = false
  const promise = (async () => {
    try {
      const url = new URL(`/sessions/${sessionId}/events`, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`)
      if (options?.replay) {
        url.searchParams.set("replay", "1")
      }
      if (options?.limit && Number.isFinite(options.limit)) {
        url.searchParams.set("limit", String(options.limit))
      }
      if (options?.fromId) {
        url.searchParams.set("from_id", options.fromId)
      }
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok || !response.body) {
        stream.write(`[sse] Failed to attach (status ${response.status})\n`)
        return
      }
      const reader = response.body.getReader()
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        if (value) {
          stream.write(Buffer.from(value).toString("utf8"))
          didWrite = true
        }
      }
    } catch (error) {
      if (aborted && !didWrite) {
        stream.write("[sse] Stream aborted after timeout while waiting for events.\n")
      } else {
        stream.write(`[sse] ${String((error as Error).message)}\n`)
      }
    } finally {
      stream.end()
    }
  })()
  return {
    promise,
    stop: () => {
      aborted = true
      controller.abort()
    },
    hasData: () => didWrite,
  }
}

const readLinesSafe = async (filePath: string): Promise<string[]> => {
  try {
    const contents = await fs.readFile(filePath, "utf8")
    return contents.split(/\r?\n/)
  } catch {
    return []
  }
}

const extractSessionIdFromStateDump = async (stateDumpPath: string): Promise<string | null> => {
  const lines = await readLinesSafe(stateDumpPath)
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i]
    if (!line || !line.trim()) continue
    try {
      const payload = JSON.parse(line) as { state?: { sessionId?: string } }
      const sessionId = payload?.state?.sessionId
      if (typeof sessionId === "string" && sessionId.trim()) {
        return sessionId.trim()
      }
    } catch {
      continue
    }
  }
  return null
}

const resolveCaseList = (cases: ReadonlyArray<string> | null, includeLive: boolean): StressCase[] => {
  if (!cases) {
    return includeLive ? STRESS_CASES : STRESS_CASES.filter((entry) => !entry.requiresLive)
  }
  const map = new Map(STRESS_CASES.map((entry) => [entry.id, entry]))
  const resolved: StressCase[] = []
  for (const key of cases) {
    const value = map.get(key)
    if (!value) {
      throw new Error(`Unknown case id "${key}". Known cases: ${STRESS_CASES.map((c) => c.id).join(", ")}`)
    }
    resolved.push(value)
  }
  return resolved
}

const copyScriptFile = async (scriptPath: string, targetDir: string) => {
  const source = path.isAbsolute(scriptPath) ? scriptPath : path.join(ROOT_DIR, scriptPath)
  const destination = path.join(targetDir, path.basename(scriptPath))
  await fs.copyFile(source, destination)
  return { source, destination }
}

const assertPath = async (target: string, kind: "file" | "dir", caseId: string, label: string) => {
  try {
    const stats = await fs.stat(target)
    if (kind === "file" && !stats.isFile()) {
      throw new Error(`${target} is not a file`)
    }
    if (kind === "dir" && !stats.isDirectory()) {
      throw new Error(`${target} is not a directory`)
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`[stress:${caseId}] Missing ${kind} for ${label}: ${message}`)
  }
}

const copyClipboardManifest = async (source: string | null | undefined, caseId: string, batchDir: string) => {
  if (!source) return null
  try {
    const contents = await fs.readFile(source, "utf8")
    const parsed = JSON.parse(contents) as { clipboard?: Record<string, unknown> | null }
    if (!parsed || !parsed.clipboard) return null
    const destDir = path.join(batchDir, "clipboard_manifests")
    await fs.mkdir(destDir, { recursive: true })
    const destPath = path.join(destDir, `${caseId}.json`)
    await fs.writeFile(destPath, contents, "utf8")
    return destPath
  } catch (error) {
    console.warn(`[stress] clipboard manifest copy failed for ${caseId}: ${(error as Error).message}`)
    return null
  }
}

const readClipboardMetadata = async (manifestPath: string) => {
  try {
    const contents = await fs.readFile(manifestPath, "utf8")
    const parsed = JSON.parse(contents) as { clipboard?: Record<string, unknown> | null }
    const clipboard = (parsed?.clipboard ?? null) as Record<string, unknown> | null
    if (!clipboard) return null
    return {
      length: clipboard.length ?? null,
      bytes: clipboard.bytes ?? null,
      mime: clipboard.mime ?? null,
      averageColor: clipboard.averageColor ?? null,
      sha256: clipboard.sha256 ?? null,
    }
  } catch {
    return null
  }
}

const diffClipboardMetadata = (previous: Record<string, unknown> | null, current: Record<string, unknown> | null) => {
  if (!previous || !current) return null
  const keys: Array<keyof typeof current> = ["length", "bytes", "mime", "averageColor", "sha256"]
  const delta: Record<string, { previous: unknown; current: unknown }> = {}
  for (const key of keys) {
    if (previous[key] !== current[key]) {
      delta[key] = { previous: previous[key], current: current[key] }
    }
  }
  return Object.keys(delta).length > 0 ? delta : null
}

const findPreviousBatchDir = async (baseDir: string, currentName: string) => {
  try {
    const entries = await fs.readdir(baseDir, { withFileTypes: true })
    const dirs = entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
    const index = dirs.indexOf(currentName)
    if (index <= 0) return null
    return path.join(baseDir, dirs[index - 1])
  } catch {
    return null
  }
}

const compareClipboardAgainstPrevious = async (
  batchDir: string,
  outDir: string,
  caseSummaries: Array<Record<string, unknown>>,
) => {
  const currentName = path.basename(batchDir)
  const previousDir = await findPreviousBatchDir(outDir, currentName)
  if (!previousDir) return []
  const results: Array<{ caseId: string; diffFile: string; changes: Record<string, { previous: unknown; current: unknown }> }> = []
  for (const caseSummary of caseSummaries) {
    const manifestRel = caseSummary.clipboardManifestPath as string | undefined
    if (!manifestRel) continue
    const currentPath = path.isAbsolute(manifestRel) ? manifestRel : path.join(batchDir, manifestRel)
    const previousPath = path.join(previousDir, "clipboard_manifests", `${caseSummary.id}.json`)
    try {
      await fs.access(previousPath)
    } catch {
      continue
    }
    const previous = await readClipboardMetadata(previousPath)
    const current = await readClipboardMetadata(currentPath)
    const diff = diffClipboardMetadata(previous, current)
    const diffDir = path.join(batchDir, "clipboard_diffs")
    await fs.mkdir(diffDir, { recursive: true })
    const diffFile = path.join(diffDir, `${caseSummary.id}.json`)
    if (diff) {
      await fs.writeFile(
        diffFile,
        JSON.stringify({ caseId: caseSummary.id, previousPath, currentPath, diff }, null, 2),
        "utf8",
      )
      results.push({ caseId: caseSummary.id as string, diffFile: path.relative(batchDir, diffFile), changes: diff })
    } else {
      await fs.rm(diffFile, { force: true }).catch(() => undefined)
    }
  }
  return results
}

const validateReplArtifacts = async (caseDir: string, caseId: string) => {
  const requiredFiles = [
    "transcript.txt",
    "cli.log",
    "sse_events.txt",
    "events.ndjson",
    "config.json",
    "contract_report.json",
    "metrics.json",
    "repl_state.ndjson",
    "timeline.ndjson",
    "timeline_summary.json",
    "timeline_flamegraph.txt",
    "ttydoc.txt",
    "case_info.json",
  ]
  await Promise.all(
    requiredFiles.map((relative) => assertPath(path.join(caseDir, relative), "file", caseId, relative)),
  )
}

const validatePtyArtifacts = async (caseDir: string, caseId: string) => {
  await assertPath(path.join(caseDir, "grid_snapshots"), "dir", caseId, "grid_snapshots")
  const requiredFiles = [
    "pty_snapshots.txt",
    "pty_plain.txt",
    "pty_raw.ansi",
    "pty_metadata.json",
    "pty_frames.ndjson",
    "pty_manifest.json",
    "input_log.ndjson",
    "repl_state.ndjson",
    "sse_events.txt",
    "events.ndjson",
    "config.json",
    "contract_report.json",
    "metrics.json",
    "grid_snapshots/active.txt",
    "grid_snapshots/final.txt",
    "grid_snapshots/final_vs_active.diff",
    "grid_deltas.ndjson",
    "anomalies.json",
    "timeline.ndjson",
    "timeline_summary.json",
    "timeline_flamegraph.txt",
    "ttydoc.txt",
    "case_info.json",
  ]
  await Promise.all(
    requiredFiles.map((relative) => assertPath(path.join(caseDir, relative), "file", caseId, relative)),
  )
}

const validateEmulatorArtifacts = async (caseDir: string, caseId: string) => {
  await assertPath(path.join(caseDir, "observer_text"), "dir", caseId, "observer_text")
  await assertPath(path.join(caseDir, "observer_png"), "dir", caseId, "observer_png")
  await assertPath(path.join(caseDir, "observer_runtime"), "dir", caseId, "observer_runtime")
  const requiredFiles = [
    "observer_runtime/wezterm-events.ndjson",
    "emulator_snapshots.txt",
    "emulator_plain.txt",
    "emulator_metadata.json",
    "emulator_frames.ndjson",
    "emulator_manifest.json",
    "input_log.ndjson",
    "repl_state.ndjson",
    "sse_events.txt",
    "events.ndjson",
    "config.json",
    "contract_report.json",
    "metrics.json",
    "anomalies.json",
    "timeline.ndjson",
    "timeline_summary.json",
    "timeline_flamegraph.txt",
    "ttydoc.txt",
    "case_info.json",
  ]
  await Promise.all(
    requiredFiles.map((relative) => assertPath(path.join(caseDir, relative), "file", caseId, relative)),
  )
}

const readJsonFile = async <T>(filePath: string): Promise<T | null> => {
  try {
    const contents = await fs.readFile(filePath, "utf8")
    return contents.trim().length > 0 ? (JSON.parse(contents) as T) : null
  } catch {
    return null
  }
}

const buildMetricsReport = async (
  summaryPath: string | null,
  contractReport: { summary?: Record<string, unknown>; errors?: unknown[]; warnings?: unknown[] },
) => {
  const timelineSummary = summaryPath ? await readJsonFile<Record<string, unknown>>(summaryPath) : null
  return {
    timelineSummary,
    contract: contractReport?.summary ?? null,
    contractIssues: {
      errors: Array.isArray(contractReport?.errors) ? contractReport.errors.length : 0,
      warnings: Array.isArray(contractReport?.warnings) ? contractReport.warnings.length : 0,
    },
  }
}

const resolveConfigPath = (testCase: BaseCase, options: CliOptions): string => {
  // P4 real-terminal evidence cases need to pin an explicit config even when the
  // batch is running with fresh-live bridge startup enabled.
  if (testCase.kind === "terminal" && testCase.configPath) {
    return resolveRepoPath(testCase.configPath)
  }
  const raw = resolveStressCaseConfigPath({
    caseConfigPath: testCase.configPath,
    requiresLive: testCase.requiresLive,
    defaultConfigPath: options.configPath,
    liveConfigPath: options.liveConfigPath,
  })
  return resolveRepoPath(raw)
}

const runReplCase = async (
  testCase: ReplCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
): Promise<Record<string, unknown>> => {
  const configPath = resolveConfigPath(testCase, options)
  const baseUrl = testCase.baseUrl ?? options.baseUrl
  const copiedScript = await copyScriptFile(testCase.script, caseDir)
  const scriptHash = await computeScriptHash(copiedScript.source)
  const reproScriptPath = toRepoRootPath(copiedScript.destination)
  const stateDumpPath = path.join(caseDir, "repl_state.ndjson")
  const viewportResetLogPath = path.join(caseDir, "viewport_resets.ndjson")
  const surfaceModelLogPath = path.join(caseDir, "surface_model.ndjson")
  const scrollbackFeedLogPath = path.join(caseDir, "scrollback_feed.ndjson")
  const markdownMetricsLogPath = path.join(caseDir, "markdown_metrics.ndjson")
  const renderTimelineLogPath = path.join(caseDir, "render_timeline.ndjson")
  const appStartAnchorPath = path.join(caseDir, "app_start_anchor.txt")
  const managedRegionBoundsLogPath = path.join(caseDir, "managed_region_bounds.ndjson")
  const scrollbackClauseVerdictsPath = path.join(caseDir, "scrollback_clause_verdicts.json")
  const caseInfoPath = path.join(caseDir, "case_info.json")
  await fs.writeFile(stateDumpPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(viewportResetLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(surfaceModelLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(scrollbackFeedLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(markdownMetricsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(renderTimelineLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(appStartAnchorPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(managedRegionBoundsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(
    scrollbackClauseVerdictsPath,
    `${JSON.stringify(buildInitialScrollbackClauseVerdicts(testCase.env?.BREADBOARD_TUI_LIVE_SHELL_MODE ?? null), null, 2)}\n`,
    "utf8",
  ).catch(() => undefined)
  const envOverrides = { ...(testCase.env ?? {}) }
  const reproEnv = {
    BREADBOARD_API_URL: baseUrl,
    BREADBOARD_STATE_DUMP_PATH: stateDumpPath,
    BREADBOARD_STATE_DUMP_MODE: "summary",
    BREADBOARD_STATE_DUMP_RATE_MS: "100",
    BREADBOARD_TUI_VIEWPORT_RESETS_FILE: viewportResetLogPath,
    BREADBOARD_TUI_SURFACE_MODEL_FILE: surfaceModelLogPath,
    BREADBOARD_TUI_SCROLLBACK_FEED_FILE: scrollbackFeedLogPath,
    BREADBOARD_TUI_MARKDOWN_METRICS_FILE: markdownMetricsLogPath,
    BREADBOARD_TUI_RENDER_TIMELINE_FILE: renderTimelineLogPath,
    BREADBOARD_TUI_APP_START_ANCHOR_FILE: appStartAnchorPath,
    BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE: managedRegionBoundsLogPath,
    BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE: scrollbackClauseVerdictsPath,
    ...envOverrides,
  }
  const baseRepro = buildReproInfo(
    {
      cwd: REPRO_CWD,
      env: reproEnv,
      argv: [
        "node",
        "dist/main.js",
        "repl",
        "--config",
        configPath,
        "--script",
        reproScriptPath,
        "--script-output",
        toRepoRootPath(path.join(caseDir, "transcript.txt")),
        "--script-final-only",
        "--script-max-duration-ms",
        String(REPL_SCRIPT_MAX_DURATION_MS),
      ],
    },
    (() => {
      const mode = typeof chaosInfo?.mode === "string" ? chaosInfo.mode : null
      if (mode !== "mock-sse") return undefined
      const script = typeof chaosInfo?.script === "string" ? chaosInfo.script : null
      const port = typeof chaosInfo?.port === "number" ? chaosInfo.port : null
      const host = port ? baseUrl.replace(/^https?:\/\//, "").split(":")[0] : null
      if (!script || !port || !host) return undefined
      const delayMultiplier = typeof chaosInfo?.delayMultiplier === "number" ? chaosInfo.delayMultiplier : 1
      const jitterMs = typeof chaosInfo?.jitterMs === "number" ? chaosInfo.jitterMs : 0
      const dropRate = typeof chaosInfo?.dropRate === "number" ? chaosInfo.dropRate : 0
      const loop = chaosInfo?.loop === true
      return {
        prelude: [
          {
            label: "start mock SSE server",
            cwd: REPRO_CWD,
            env: {},
            argv: [
              "npx",
              "tsx",
              "tools/mock/mockSseServer.ts",
              "--script",
              toRepoRootPath(script),
              "--host",
              host,
              "--port",
              String(port),
              ...(loop ? ["--loop"] : []),
              "--delay-multiplier",
              String(delayMultiplier),
              "--jitter-ms",
              String(jitterMs),
              "--drop-rate",
              String(dropRate),
            ],
          },
        ],
        notes: ["Run the prelude command in a separate terminal (or background it) before running the main repro command."],
      }
    })(),
  )
  await fs.writeFile(
    caseInfoPath,
    `${JSON.stringify(
      {
        id: testCase.id,
        kind: testCase.kind,
        description: testCase.description,
        script: testCase.script,
        scriptHash,
        repro: baseRepro,
        config: configPath,
        chaos: chaosInfo,
        env: envOverrides,
        contractOverrides: options.contractOverrides,
      },
      null,
      2,
    )}\n`,
    "utf8",
  )
  const transcriptPath = path.join(caseDir, "transcript.txt")
  const logPath = path.join(caseDir, "cli.log")
  const ssePath = path.join(caseDir, "sse_events.txt")
  const eventsNdjsonPath = path.join(caseDir, "events.ndjson")
  const configOutPath = path.join(caseDir, "config.json")
  const contractReportPath = path.join(caseDir, "contract_report.json")
  const metricsPath = path.join(caseDir, "metrics.json")
  const stateDumpEnvPath = stateDumpPath
  const args = [
    "dist/main.js",
    "repl",
    "--config",
    configPath,
    "--script",
    testCase.script,
    "--script-output",
    transcriptPath,
    "--script-final-only",
    "--script-max-duration-ms",
    String(REPL_SCRIPT_MAX_DURATION_MS),
  ]
  if (testCase.model) {
    args.push("--model", testCase.model)
  }
  const env = {
    ...process.env,
    BREADBOARD_API_URL: baseUrl,
    BREADBOARD_STATE_DUMP_PATH: stateDumpEnvPath,
    BREADBOARD_STATE_DUMP_MODE: "summary",
    BREADBOARD_STATE_DUMP_RATE_MS: "100",
    ...envOverrides,
  }
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "codex_v1"
  await fs.writeFile(
    configOutPath,
    JSON.stringify(
      {
        configPath,
        baseUrl,
        command: options.command,
        profile,
        env: envOverrides,
        contractOverrides: options.contractOverrides,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )
  let sseHandle: { promise: Promise<void>; stop: () => void } | null = null
  const onStdoutChunk = (chunk: string) => {
    if (sseHandle) return
    const match = chunk.match(/Script session\s+([0-9a-fA-F-]+)/)
    if (match) {
      sseHandle = tapSessionEvents(baseUrl, match[1], ssePath)
    }
  }
  await spawnLoggedProcess("node", args, { cwd: ROOT_DIR, env }, logPath, {
    onStdoutChunk,
  })
  if (sseHandle) {
    await Promise.race([
      sseHandle.promise,
      new Promise<void>((resolve) => {
        setTimeout(() => {
          sseHandle.stop()
          resolve()
        }, SSE_WAIT_TIMEOUT_MS)
      }),
    ])
    sseHandle.stop()
    await sseHandle.promise.catch(() => undefined)
    const stats = await fs.stat(ssePath).catch(() => null)
    if (!stats || stats.size === 0) {
      await writeFallbackMessage(
        ssePath,
        "[sse] Stream closed without emitting data. Session finished before events could be captured.",
      )
    }
  } else {
    await writeFallbackMessage(ssePath, "[sse] Session id not observed in CLI output.")
  }
  await writeEventsNdjson(ssePath, eventsNdjsonPath)
  const flamegraphPath = path.join(caseDir, "timeline_flamegraph.txt")
  const ttyDocPath = path.join(caseDir, "ttydoc.txt")
  const attachmentSummaryPath = testCase.id === "attachment_submit" ? path.join(caseDir, "attachments_summary.json") : undefined
  const timelinePaths = await buildTimelineArtifacts(caseDir, {
    metadataPath: null,
    gridDeltaPath: null,
    ssePath,
    chaosInfo,
  })
  if (!timelinePaths) {
    throw new Error(`[stress:${testCase.id}] Failed to build timeline artifacts in ${caseDir}`)
  }
  await buildFlamegraph(timelinePaths.timelinePath, flamegraphPath)
  await buildTtyDoc({
    caseDir,
    caseId: testCase.id,
    scriptPath: testCase.script,
    configPath: configPath,
  })
  const contractReport = await runContractChecks(ssePath, options.contractOverrides ?? undefined)
  await fs.writeFile(contractReportPath, JSON.stringify(contractReport, null, 2), "utf8")
  const metricsReport = await buildMetricsReport(timelinePaths.summaryPath, contractReport)
  await fs.writeFile(metricsPath, JSON.stringify(metricsReport, null, 2), "utf8")
  await validateReplArtifacts(caseDir, testCase.id)
  const stats = {
    transcriptPath,
    logPath,
    ssePath,
    eventsNdjsonPath,
    configPath: configOutPath,
    contractReportPath,
    metricsPath,
    stateDumpPath,
    timelinePath: timelinePaths.timelinePath,
    timelineSummaryPath: timelinePaths.summaryPath,
    timelineFlamegraphPath: flamegraphPath,
    ttyDocPath,
    caseInfoPath,
    chaosInfo,
    outputs: {
      transcript: transcriptPath,
      log: logPath,
      sse: ssePath,
      sseEventsNdjson: eventsNdjsonPath,
      config: configOutPath,
      contractReport: contractReportPath,
      metrics: metricsPath,
      replState: stateDumpPath,
      viewportResets: viewportResetLogPath,
      surfaceModel: surfaceModelLogPath,
      scrollbackFeed: scrollbackFeedLogPath,
      markdownMetrics: markdownMetricsLogPath,
      renderTimeline: renderTimelineLogPath,
      appStartAnchor: appStartAnchorPath,
      managedRegionBounds: managedRegionBoundsLogPath,
      scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
      timeline: timelinePaths.timelinePath,
      timelineSummary: timelinePaths.summaryPath,
      timelineFlamegraph: flamegraphPath,
      ttydoc: ttyDocPath,
      caseInfo: caseInfoPath,
    },
  }
  return stats
}


type ScrollbackClauseStatus = "pass" | "fail" | "not_applicable"

const buildInitialScrollbackClauseVerdicts = (mode: string | null = null) => ({
  contractVersion: 1,
  clauseSetVersion: 1,
  mode,
  clausesEvaluated: [],
  verdicts: [],
})

const buildFinalScrollbackClauseVerdicts = (args: {
  readonly mode: string | null
  readonly caseId: string
  readonly terminalLane: TerminalAdapterLane
  readonly anomalies: ReadonlyArray<LayoutAnomaly>
}) => {
  const anomalyIds = new Set(args.anomalies.map((entry) => entry.id))
  const hasAny = (needles: ReadonlyArray<string>): boolean =>
    [...anomalyIds].some((id) => needles.some((needle) => id.includes(needle)))
  const verdicts: Array<{
    id: string
    status: ScrollbackClauseStatus
    evidence: string[]
    blocking: boolean
  }> = []

  const add = (id: string, status: ScrollbackClauseStatus, evidence: string[], blocking = status === "fail") => {
    verdicts.push({ id, status, evidence, blocking })
  }
  const addP11 = (id: string, failedNeedles: ReadonlyArray<string>) => {
    const evidence = [...anomalyIds].filter((anomalyId) => failedNeedles.some((needle) => anomalyId.includes(needle)))
    add(id, evidence.length > 0 ? "fail" : "pass", evidence, true)
  }

  if (args.caseId.startsWith("p11_")) {
    addP11("GNS-03", ["clear-screen", "clear-scrollback", "terminal-reset", "alt-buffer"])
    addP11("GNS-18", ["ui-chrome", "runtime-chrome", "prototype", "scene-owned"])
    addP11("GNS-20", ["clause-completeness"])

    if (args.caseId.includes("history_guard")) {
      addP11("GNS-01", ["pre-app-history", "SCR-HIST", "anchor-history-policy", "anchor-mode"])
      addP11("GNS-04", ["managed-region-bounds-missing", "static-feed", "reclaim"])
      addP11("GNS-14", ["landing", "anchor"])
    }

    if (args.caseId.includes("startup")) {
      addP11("GNS-02", ["startup", "landing", "blank-gap"])
      addP11("GNS-13", ["composer", "footer", "prompt", "blank-gap"])
      addP11("GNS-14", ["landing"])
    }

    if (args.caseId.includes("width_equiv") || args.caseId.includes("width_shrink")) {
      addP11("GNS-05", ["visible", "collision", "stale", "duplicate", "compact-transcript"])
      addP11("GNS-06", ["visible-near-blank", "visible-turn-context-missing", "visible-duplicate", "horizontal", "clipped"])
      addP11("GNS-11", ["table", "markdown", "code", "interrupted"])
      addP11("GNS-13", ["composer", "footer", "prompt", "blank-gap"])
      addP11("GNS-19", ["width-equivalence"])
    }

    if (args.caseId.includes("multiturn")) {
      addP11("GNS-10", ["turn2-context-missing", "duplicate", "committed"])
    }

    if (args.caseId.includes("height")) {
      addP11("GNS-08", ["height", "near-blank", "context-missing", "blank-gap"])
      addP11("GNS-13", ["composer", "footer", "prompt", "blank-gap"])
    }

    if (args.caseId.includes("churn")) {
      addP11("GNS-09", ["churn", "duplicate", "stale", "clipped", "collision"])
      addP11("GNS-13", ["composer", "footer", "prompt", "blank-gap"])
    }

    if (args.caseId.includes("paste")) {
      addP11("GNS-16", ["bracketed-paste"])
    }

    return {
      contractVersion: 1,
      clauseSetVersion: 11,
      mode: args.mode,
      terminalLane: args.terminalLane,
      caseId: args.caseId,
      clausesEvaluated: verdicts.map((entry) => entry.id),
      verdicts,
    }
  }

  if (args.caseId.includes("seeded_host_history_guard")) {
    const failed = hasAny(["pre-app-history-missing", "anchor-history-policy-mismatch", "anchor-mode-mismatch"])
    add("pre_app_host_history_preservation", failed ? "fail" : "pass", [...anomalyIds])
  }

  if (args.caseId.includes("width_shrink")) {
    const visibleNeedles = [
      "visible-near-blank",
      "visible-turn-context-missing",
      "visible-duplicate",
      "compact-transcript-fallback",
      "turn2-context-missing",
      "hidden-history-cue",
    ]
    const scrollbackNeedles = [
      "scrollback-duplicate",
      "scrollback-turn-context-missing",
    ]
    const visibleEvidence = [...anomalyIds].filter((id) => visibleNeedles.some((needle) => id.includes(needle)))
    const scrollbackEvidence = [...anomalyIds].filter((id) => scrollbackNeedles.some((needle) => id.includes(needle)))
    add("width_shrink_current_visible_region", visibleEvidence.length > 0 ? "fail" : "pass", visibleEvidence)
    add("width_shrink_scrollback_history_purity", scrollbackEvidence.length > 0 ? "fail" : "pass", scrollbackEvidence)
  }

  if (args.caseId.includes("height_change")) {
    const heightNeedles = [
      "height",
      "near-blank",
      "context-missing",
      "blank-gap",
      "composer",
      "footer",
      "prompt",
      "startup",
    ]
    const evidence = [...anomalyIds].filter((id) => heightNeedles.some((needle) => id.includes(needle)))
    add("height_change_visible_region_integrity", evidence.length > 0 ? "fail" : "pass", evidence)
  }

  if (args.caseId.includes("resize_churn")) {
    const churnNeedles = [
      "churn",
      "duplicate",
      "stale",
      "clipped",
      "collision",
      "near-blank",
      "context-missing",
      "blank-gap",
      "composer",
      "footer",
      "prompt",
      "startup",
    ]
    const evidence = [...anomalyIds].filter((id) => churnNeedles.some((needle) => id.includes(needle)))
    add("resize_churn_visible_region_integrity", evidence.length > 0 ? "fail" : "pass", evidence)
  }

  return {
    contractVersion: 1,
    clauseSetVersion: 2,
    mode: args.mode,
    terminalLane: args.terminalLane,
    caseId: args.caseId,
    clausesEvaluated: verdicts.map((entry) => entry.id),
    verdicts,
  }
}

const writeFrames = async (frames: ReadonlyArray<{ timestamp: number; chunk: string }>, target: string) => {
  const lines = frames.map((frame) => JSON.stringify(frame))
  await fs.writeFile(target, `${lines.join("\n")}\n`, "utf8")
}

const runPtyCase = async (
  testCase: PtyCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
): Promise<Record<string, unknown>> => {
  const copiedScript = await copyScriptFile(testCase.script, caseDir)
  const scriptHash = await computeScriptHash(copiedScript.source)
  const reproScriptPath = toRepoRootPath(copiedScript.destination)
  const caseInfoPath = path.join(caseDir, "case_info.json")
  const inputLogPath = path.join(caseDir, "input_log.ndjson")
  const stateDumpPath = path.join(caseDir, "repl_state.ndjson")
  const viewportResetLogPath = path.join(caseDir, "viewport_resets.ndjson")
  const surfaceModelLogPath = path.join(caseDir, "surface_model.ndjson")
  const scrollbackFeedLogPath = path.join(caseDir, "scrollback_feed.ndjson")
  const markdownMetricsLogPath = path.join(caseDir, "markdown_metrics.ndjson")
  const renderTimelineLogPath = path.join(caseDir, "render_timeline.ndjson")
  const appStartAnchorPath = path.join(caseDir, "app_start_anchor.txt")
  const managedRegionBoundsLogPath = path.join(caseDir, "managed_region_bounds.ndjson")
  const scrollbackClauseVerdictsPath = path.join(caseDir, "scrollback_clause_verdicts.json")
  // Some PTY cases may not be BreadBoard's Ink TUI (e.g. external prototypes).
  // Keep the artifact contract stable by ensuring debug streams exist.
  await fs.writeFile(stateDumpPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(viewportResetLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(surfaceModelLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(scrollbackFeedLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(markdownMetricsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(renderTimelineLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(appStartAnchorPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(managedRegionBoundsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(
    scrollbackClauseVerdictsPath,
    `${JSON.stringify(buildInitialScrollbackClauseVerdicts(testCase.env?.BREADBOARD_TUI_LIVE_SHELL_MODE ?? null), null, 2)}\n`,
    "utf8",
  ).catch(() => undefined)
  const envOverrides = { ...(testCase.env ?? {}) }
  const qcBatchId = path.basename(path.dirname(caseDir))
  const harnessEnvOverrides = {
    BREADBOARD_QC_BATCH_ID: qcBatchId,
    BREADBOARD_QC_CASE_ID: testCase.id,
    BREADBOARD_TUI_VIEWPORT_RESETS_FILE: viewportResetLogPath,
    BREADBOARD_TUI_SURFACE_MODEL_FILE: surfaceModelLogPath,
    BREADBOARD_TUI_SCROLLBACK_FEED_FILE: scrollbackFeedLogPath,
    BREADBOARD_TUI_MARKDOWN_METRICS_FILE: markdownMetricsLogPath,
    BREADBOARD_TUI_RENDER_TIMELINE_FILE: renderTimelineLogPath,
    BREADBOARD_TUI_APP_START_ANCHOR_FILE: appStartAnchorPath,
    BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE: managedRegionBoundsLogPath,
    BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE: scrollbackClauseVerdictsPath,
    ...envOverrides,
  }
  const command = testCase.command ?? options.command
  const commandCwd = resolveCaseCommandCwd(testCase, command)
  await fs.mkdir(commandCwd, { recursive: true })
  await writeCaseWorkspaceFiles(commandCwd, testCase.workspaceFiles)
  const commandProvenance = await buildCommandProvenance(command, commandCwd)
  const configPath = resolveConfigPath(testCase, options)
  const baseUrl = testCase.baseUrl ?? options.baseUrl
  const repro = buildReproInfo(
    {
      cwd: REPRO_CWD,
      env: {
        BREADBOARD_API_URL: baseUrl,
        BREADBOARD_STATE_DUMP_PATH: stateDumpPath,
        BREADBOARD_STATE_DUMP_MODE: "summary",
        BREADBOARD_STATE_DUMP_RATE_MS: "100",
        ...harnessEnvOverrides,
      },
      argv: [
        "npx",
        "tsx",
        "scripts/repl_pty_harness.ts",
        "--script",
        reproScriptPath,
        "--config",
        configPath,
        "--base-url",
        baseUrl,
        "--snapshots",
        toRepoRootPath(path.join(caseDir, "pty_snapshots.txt")),
        "--raw-log",
        toRepoRootPath(path.join(caseDir, "pty_raw.ansi")),
        "--plain-log",
        toRepoRootPath(path.join(caseDir, "pty_plain.txt")),
        "--metadata",
        toRepoRootPath(path.join(caseDir, "pty_metadata.json")),
        "--frame-log",
        toRepoRootPath(path.join(caseDir, "pty_frames.ndjson")),
        "--manifest",
        toRepoRootPath(path.join(caseDir, "pty_manifest.json")),
        "--input-log",
        toRepoRootPath(inputLogPath),
        "--cols",
        String(testCase.cols ?? 120),
        "--rows",
        String(testCase.rows ?? 36),
        "--cmd",
        command,
        ...(typeof testCase.submitTimeoutMs === "number" ? ["--submit-timeout-ms", String(testCase.submitTimeoutMs)] : []),
        ...(testCase.winchScript ? ["--winch-script", testCase.winchScript] : []),
        ...(testCase.clipboardText ? ["--clipboard-text", testCase.clipboardText] : []),
        ...(testCase.clipboardFile ? ["--clipboard-file", testCase.clipboardFile] : []),
      ],
    },
    (() => {
      const mode = typeof chaosInfo?.mode === "string" ? chaosInfo.mode : null
      if (mode !== "mock-sse") return undefined
      const script = typeof chaosInfo?.script === "string" ? chaosInfo.script : null
      const port = typeof chaosInfo?.port === "number" ? chaosInfo.port : null
      const host = port ? baseUrl.replace(/^https?:\/\//, "").split(":")[0] : null
      if (!script || !port || !host) return undefined
      const delayMultiplier = typeof chaosInfo?.delayMultiplier === "number" ? chaosInfo.delayMultiplier : 1
      const jitterMs = typeof chaosInfo?.jitterMs === "number" ? chaosInfo.jitterMs : 0
      const dropRate = typeof chaosInfo?.dropRate === "number" ? chaosInfo.dropRate : 0
      const loop = chaosInfo?.loop === true
      return {
        prelude: [
          {
            label: "start mock SSE server",
            cwd: REPRO_CWD,
            env: {},
            argv: [
              "npx",
              "tsx",
              "tools/mock/mockSseServer.ts",
              "--script",
              toRepoRootPath(script),
              "--host",
              host,
              "--port",
              String(port),
              ...(loop ? ["--loop"] : []),
              "--delay-multiplier",
              String(delayMultiplier),
              "--jitter-ms",
              String(jitterMs),
              "--drop-rate",
              String(dropRate),
            ],
          },
        ],
        notes: ["Run the prelude command in a separate terminal (or background it) before running the main repro command."],
      }
    })(),
  )
  await fs.writeFile(
    caseInfoPath,
    `${JSON.stringify(
      {
        id: testCase.id,
        kind: testCase.kind,
        description: testCase.description,
        script: testCase.script,
        scriptHash,
        repro,
        config: configPath,
        chaos: chaosInfo,
        env: envOverrides,
        contractOverrides: options.contractOverrides,
      },
      null,
      2,
    )}\n`,
    "utf8",
  )
  const snapshotsPath = path.join(caseDir, "pty_snapshots.txt")
  const rawLogPath = path.join(caseDir, "pty_raw.ansi")
  const plainLogPath = path.join(caseDir, "pty_plain.txt")
  const metadataPath = path.join(caseDir, "pty_metadata.json")
  const frameLogPath = path.join(caseDir, "pty_frames.ndjson")
  const manifestPath = path.join(caseDir, "pty_manifest.json")
  const configOutPath = path.join(caseDir, "config.json")
  const gridSnapshotDir = path.join(caseDir, "grid_snapshots")
  const gridFinalPath = path.join(gridSnapshotDir, "final.txt")
  const gridActivePath = path.join(gridSnapshotDir, "active.txt")
  const gridNormalFinalPath = path.join(gridSnapshotDir, "normal_final.txt")
  const gridNormalActivePath = path.join(gridSnapshotDir, "normal_active.txt")
  const gridAltFinalPath = path.join(gridSnapshotDir, "alt_final.txt")
  const gridAltActivePath = path.join(gridSnapshotDir, "alt_active.txt")
  const gridDeltaPath = path.join(caseDir, "grid_deltas.ndjson")
  const anomaliesPath = path.join(caseDir, "anomalies.json")
  const ssePath = path.join(caseDir, "sse_events.txt")
  const eventsNdjsonPath = path.join(caseDir, "events.ndjson")
  const contractReportPath = path.join(caseDir, "contract_report.json")
  const metricsPath = path.join(caseDir, "metrics.json")
  const gridDiffPath = path.join(gridSnapshotDir, "final_vs_active.diff")
  const flamegraphPath = path.join(caseDir, "timeline_flamegraph.txt")
  const ttyDocPath = path.join(caseDir, "ttydoc.txt")
  const attachmentSummaryPath = testCase.id === "attachment_submit" ? path.join(caseDir, "attachments_summary.json") : undefined

  const steps = await loadStepsFromFile(testCase.script)
  const clipboardPayload = testCase.clipboardText
    ? { source: "inline" as const, text: testCase.clipboardText }
    : testCase.clipboardFile
    ? {
        source: "file" as const,
        text: await fs.readFile(
          path.isAbsolute(testCase.clipboardFile)
            ? testCase.clipboardFile
            : path.join(ROOT_DIR, testCase.clipboardFile),
          "utf8",
        ),
        path: testCase.clipboardFile,
      }
    : undefined

  const winchEvents = testCase.winchScript ? await loadWinchEventsFromFile(testCase.winchScript) : undefined

  const inputLog: string[] = []
  let harnessFailure: Error | null = null
  let harnessResult: Awaited<ReturnType<typeof runSpectatorHarness>>
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "codex_v1"
  await fs.writeFile(
    configOutPath,
    JSON.stringify(
      {
        configPath,
        baseUrl,
        command,
        commandProvenance,
        profile,
        env: harnessEnvOverrides,
        contractOverrides: options.contractOverrides,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )
  try {
    harnessResult = await runSpectatorHarness({
      steps,
      command,
      cwd: commandCwd,
      configPath,
      baseUrl,
      envOverrides: harnessEnvOverrides,
      cols: testCase.cols ?? 120,
      rows: testCase.rows ?? 36,
      echo: false,
      clipboardPayload,
      submitTimeoutMs: testCase.submitTimeoutMs,
      winchEvents,
      attachmentSummaryPath,
      onInput: (entry) => inputLog.push(JSON.stringify(entry)),
      stateDumpPath,
      stateDumpMode: "summary",
      stateDumpRateMs: 100,
    })
  } catch (error) {
    if (error instanceof SpectatorHarnessError) {
      harnessResult = error.result
      harnessFailure = error
    } else {
      throw error
    }
  }

  await writeSnapshotFile(harnessResult.snapshots, snapshotsPath)
  await fs.writeFile(rawLogPath, harnessResult.rawBuffer, "utf8")
  await fs.writeFile(plainLogPath, harnessResult.plainBuffer, "utf8")
  await fs.writeFile(metadataPath, JSON.stringify(harnessResult.metadata, null, 2), "utf8")
  await writeFrames(harnessResult.frames, frameLogPath)
  await fs.writeFile(inputLogPath, inputLog.length > 0 ? `${inputLog.join("\n")}\n` : "", "utf8")
  let timelinePaths: { timelinePath: string; summaryPath: string } | null = null
  const sessionId = await extractSessionIdFromStateDump(stateDumpPath)
  if (sessionId) {
    // Use replay capture for canonical bundle truth so live runs do not miss early events
    // due to late attachment after the interactive session has already progressed.
    const sseHandle = tapSessionEvents(baseUrl, sessionId, ssePath, { replay: true, limit: 1000 })
    await Promise.race([
      sseHandle.promise,
      new Promise<void>((resolve) => {
        setTimeout(() => {
          sseHandle.stop()
          resolve()
        }, SSE_REPLAY_TIMEOUT_MS)
      }),
    ])
    sseHandle.stop()
    await sseHandle.promise.catch(() => undefined)
    if (!sseHandle.hasData()) {
      await writeFallbackMessage(ssePath, "[sse] Replay completed with no events.")
    }
  } else {
    await writeFallbackMessage(ssePath, "[sse] Session id not found in state dump.")
  }
  await writeEventsNdjson(ssePath, eventsNdjsonPath)
  const contractReport = await runContractChecks(ssePath, options.contractOverrides ?? undefined)
  await fs.writeFile(contractReportPath, JSON.stringify(contractReport, null, 2), "utf8")

  try {
    const gridRows = harnessResult.metadata.rows ?? options.rows ?? 36
    const gridCols = harnessResult.metadata.cols ?? options.cols ?? 120
    const {
      grid,
      lastActiveGrid,
      deltas,
      normalGrid,
      normalLastActiveGrid,
      alternateGrid,
      alternateLastActiveGrid,
    } = renderGridFromFrames(harnessResult.frames, gridRows, gridCols)
    await fs.mkdir(gridSnapshotDir, { recursive: true })
    await fs.writeFile(gridFinalPath, `${grid.join("\n")}\n`, "utf8")
    const activeGrid = lastActiveGrid ?? grid
    await fs.writeFile(gridActivePath, `${activeGrid.join("\n")}\n`, "utf8")
    const normalActiveGrid = normalLastActiveGrid ?? normalGrid
    const altActiveGrid = alternateLastActiveGrid ?? alternateGrid
    await fs.writeFile(gridNormalFinalPath, `${normalGrid.join("\n")}\n`, "utf8")
    await fs.writeFile(gridNormalActivePath, `${normalActiveGrid.join("\n")}\n`, "utf8")
    await fs.writeFile(gridAltFinalPath, `${alternateGrid.join("\n")}\n`, "utf8")
    await fs.writeFile(gridAltActivePath, `${altActiveGrid.join("\n")}\n`, "utf8")
    if (deltas.length > 0) {
      const deltaLines = deltas.map((entry) => JSON.stringify(entry)).join("\n")
      await fs.writeFile(gridDeltaPath, `${deltaLines}\n`, "utf8")
    } else {
      await fs.writeFile(gridDeltaPath, "", "utf8")
    }

    const activeSnapshotPath = activeGrid === grid ? gridFinalPath : gridActivePath
    const anomalies = await runLayoutAssertions(activeSnapshotPath)
    const extraAnomalies: LayoutAnomaly[] = []
    if (testCase.expectedAltText) {
      const expected = testCase.expectedAltText.toLowerCase()
      const altText = altActiveGrid.join("\n").toLowerCase()
      if (!altText.includes(expected)) {
        extraAnomalies.push({
          id: "alt-screen-missing",
          message: `Expected alternate screen to include "${testCase.expectedAltText}".`,
        })
      }
    }
    if (testCase.postCheckScript) {
      const postCheckAnomalies = await runPostCheckScript(testCase.postCheckScript, caseDir)
      extraAnomalies.push(...postCheckAnomalies)
    }
    const combinedAnomalies = [...anomalies, ...extraAnomalies]
    await fs.writeFile(anomaliesPath, `${JSON.stringify(combinedAnomalies, null, 2)}\n`, "utf8")
    if (combinedAnomalies.length > 0) {
      console.warn(`[stress] layout anomalies detected for ${testCase.id}: ${combinedAnomalies.length}`)
    }

    const [linesFinal, linesActive] = await Promise.all([readGridLines(gridFinalPath), readGridLines(activeSnapshotPath)])
    const diffResult = computeGridDiff(linesFinal, linesActive, { labelA: "final", labelB: "active" })
    await fs.writeFile(gridDiffPath, diffResult.report, "utf8")

    timelinePaths = await buildTimelineArtifacts(caseDir, { chaosInfo, ssePath, anomaliesPath })
    if (!timelinePaths) {
      throw new Error(`[stress:${testCase.id}] Failed to build timeline artifacts in ${caseDir}`)
    }
    await buildFlamegraph(timelinePaths.timelinePath, flamegraphPath)
    await buildTtyDoc({
      caseDir,
      caseId: testCase.id,
      scriptPath: testCase.script,
      configPath: options.configPath,
    })
  } catch (error) {
    console.error(`[stress] grid reconstruction failed for ${testCase.id}: ${(error as Error).message}`)
    try {
      await fs.mkdir(gridSnapshotDir, { recursive: true })
      await fs.writeFile(gridFinalPath, "", "utf8")
      await fs.writeFile(gridActivePath, "", "utf8")
      await fs.writeFile(gridNormalFinalPath, "", "utf8")
      await fs.writeFile(gridNormalActivePath, "", "utf8")
      await fs.writeFile(gridAltFinalPath, "", "utf8")
      await fs.writeFile(gridAltActivePath, "", "utf8")
      await fs.writeFile(gridDeltaPath, "", "utf8")
      await fs.writeFile(anomaliesPath, `${JSON.stringify([
        { id: "grid-error", message: (error as Error).message },
      ])}\n`, "utf8")
      await fs.writeFile(gridDiffPath, "grid diff unavailable due to error\n", "utf8")
      await fs.writeFile(flamegraphPath, "timeline unavailable due to error\n", "utf8")
    } catch (writeError) {
      console.error(`[stress] failed to persist grid error artifacts: ${(writeError as Error).message}`)
    }
  }

  const metricsReport = await buildMetricsReport(timelinePaths?.summaryPath ?? null, contractReport)
  await fs.writeFile(metricsPath, JSON.stringify(metricsReport, null, 2), "utf8")

  await fs.writeFile(
    manifestPath,
    JSON.stringify(
      {
        script: testCase.script,
        clipboard: harnessResult.clipboard ?? null,
        outputs: {
          snapshots: snapshotsPath,
          raw: rawLogPath,
          plain: plainLogPath,
          metadata: metadataPath,
          config: configOutPath,
          frames: frameLogPath,
          inputLog: inputLogPath,
          replState: stateDumpPath,
          viewportResets: viewportResetLogPath,
          surfaceModel: surfaceModelLogPath,
          scrollbackFeed: scrollbackFeedLogPath,
          markdownMetrics: markdownMetricsLogPath,
          renderTimeline: renderTimelineLogPath,
          appStartAnchor: appStartAnchorPath,
          managedRegionBounds: managedRegionBoundsLogPath,
          scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
          gridActive: gridActivePath,
          gridFinal: gridFinalPath,
          gridNormalFinal: gridNormalFinalPath,
          gridNormalActive: gridNormalActivePath,
          gridAltFinal: gridAltFinalPath,
          gridAltActive: gridAltActivePath,
          gridDeltas: gridDeltaPath,
          gridDiff: gridDiffPath,
          anomalies: anomaliesPath,
          timeline: timelinePaths?.timelinePath ?? null,
          timelineSummary: timelinePaths?.summaryPath ?? null,
          timelineFlamegraph: timelinePaths ? flamegraphPath : null,
          ttydoc: timelinePaths ? ttyDocPath : null,
          sse: ssePath,
          sseEventsNdjson: eventsNdjsonPath,
          metrics: metricsPath,
          contractReport: contractReportPath,
        },
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )

  await validatePtyArtifacts(caseDir, testCase.id)

  if (testCase.clipboardAssertions) {
    await runClipboardAssertions(caseDir, testCase.clipboardAssertions)
  }

  if (harnessFailure) {
    throw harnessFailure
  }

  return {
    snapshotsPath,
    rawLogPath,
    plainLogPath,
    metadataPath,
    frameLogPath,
    inputLogPath,
    stateDumpPath,
    manifestPath,
    metricsPath,
    contractReportPath,
    eventsNdjsonPath,
    configPath: configOutPath,
    gridSnapshotDir,
    gridDeltaPath,
    anomaliesPath,
    timelinePath: timelinePaths?.timelinePath ?? null,
    timelineSummaryPath: timelinePaths?.summaryPath ?? null,
    timelineFlamegraphPath: timelinePaths ? flamegraphPath : null,
    ttyDocPath: timelinePaths ? ttyDocPath : null,
    chaosInfo,
    commandProvenance,
    outputs: {
      snapshots: snapshotsPath,
      raw: rawLogPath,
      plain: plainLogPath,
      metadata: metadataPath,
      config: configOutPath,
      frames: frameLogPath,
      inputLog: inputLogPath,
      replState: stateDumpPath,
      viewportResets: viewportResetLogPath,
      surfaceModel: surfaceModelLogPath,
      scrollbackFeed: scrollbackFeedLogPath,
      markdownMetrics: markdownMetricsLogPath,
      renderTimeline: renderTimelineLogPath,
      appStartAnchor: appStartAnchorPath,
      managedRegionBounds: managedRegionBoundsLogPath,
      scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
      gridActive: gridActivePath,
      gridFinal: gridFinalPath,
      gridNormalFinal: gridNormalFinalPath,
      gridNormalActive: gridNormalActivePath,
      gridAltFinal: gridAltFinalPath,
      gridAltActive: gridAltActivePath,
      gridDeltas: gridDeltaPath,
      gridDiff: gridDiffPath,
      anomalies: anomaliesPath,
      timeline: timelinePaths?.timelinePath ?? null,
      timelineSummary: timelinePaths?.summaryPath ?? null,
      timelineFlamegraph: timelinePaths ? flamegraphPath : null,
      ttydoc: timelinePaths ? ttyDocPath : null,
      sse: ssePath,
      sseEventsNdjson: eventsNdjsonPath,
      metrics: metricsPath,
      contractReport: contractReportPath,
      config: configOutPath,
      caseInfo: caseInfoPath,
    },
  }
}

const validateTerminalArtifacts = async (caseDir: string, caseId: string, lane: TerminalAdapterLane) => {
  await assertPath(path.join(caseDir, "terminal_text"), "dir", caseId, "terminal_text")
  const requiredFiles = [
    "terminal_snapshots.txt",
    "terminal_plain.txt",
    "terminal_metadata.json",
    "terminal_events.ndjson",
    "terminal_frames.ndjson",
    "input_log.ndjson",
    "terminal_manifest.json",
    "config.json",
    "contract_report.json",
    "metrics.json",
    "anomalies.json",
    "case_info.json",
    "terminal_text/visible_final.txt",
    "terminal_text/scrollback_final.txt",
    "app_start_anchor.txt",
    "managed_region_bounds.ndjson",
    "scrollback_clause_verdicts.json",
  ]
  if (lane === "wezterm") {
    requiredFiles.push(
      "repl_state.ndjson",
      "sse_events.txt",
      "events.ndjson",
      "timeline.ndjson",
      "timeline_summary.json",
      "timeline_flamegraph.txt",
      "ttydoc.txt",
    )
    await assertPath(path.join(caseDir, "observer_text"), "dir", caseId, "observer_text")
    await assertPath(path.join(caseDir, "observer_png"), "dir", caseId, "observer_png")
    await assertPath(path.join(caseDir, "observer_runtime"), "dir", caseId, "observer_runtime")
  }
  if (lane === "ghostty") {
    await assertPath(path.join(caseDir, "observer_text"), "dir", caseId, "observer_text")
    await assertPath(path.join(caseDir, "observer_png"), "dir", caseId, "observer_png")
    await assertPath(path.join(caseDir, "observer_runtime"), "dir", caseId, "observer_runtime")
  }
  await Promise.all(requiredFiles.map((relative) => assertPath(path.join(caseDir, relative), "file", caseId, relative)))
}

const runTerminalCase = async (
  testCase: TerminalCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
): Promise<Record<string, unknown>> => {
  const copiedScript = await copyScriptFile(testCase.script, caseDir)
  const scriptHash = await computeScriptHash(copiedScript.source)
  const reproScriptPath = toRepoRootPath(copiedScript.destination)
  const caseInfoPath = path.join(caseDir, "case_info.json")
  const inputLogPath = path.join(caseDir, "input_log.ndjson")
  const frameLogPath = path.join(caseDir, "terminal_frames.ndjson")
  const stateDumpPath = path.join(caseDir, "repl_state.ndjson")
  const viewportResetLogPath = path.join(caseDir, "viewport_resets.ndjson")
  const surfaceModelLogPath = path.join(caseDir, "surface_model.ndjson")
  const scrollbackFeedLogPath = path.join(caseDir, "scrollback_feed.ndjson")
  const markdownMetricsLogPath = path.join(caseDir, "markdown_metrics.ndjson")
  const renderTimelineLogPath = path.join(caseDir, "render_timeline.ndjson")
  const appStartAnchorPath = path.join(caseDir, "app_start_anchor.txt")
  const managedRegionBoundsLogPath = path.join(caseDir, "managed_region_bounds.ndjson")
  const scrollbackClauseVerdictsPath = path.join(caseDir, "scrollback_clause_verdicts.json")
  await fs.writeFile(inputLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(frameLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(stateDumpPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(viewportResetLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(surfaceModelLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(scrollbackFeedLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(markdownMetricsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(renderTimelineLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(appStartAnchorPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(managedRegionBoundsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(
    scrollbackClauseVerdictsPath,
    `${JSON.stringify(buildInitialScrollbackClauseVerdicts(testCase.env?.BREADBOARD_TUI_LIVE_SHELL_MODE ?? null), null, 2)}\n`,
    "utf8",
  ).catch(() => undefined)

  const envOverrides = { ...(testCase.env ?? {}) }
  const qcBatchId = path.basename(path.dirname(caseDir))
  const harnessEnvOverrides = {
    BREADBOARD_QC_BATCH_ID: qcBatchId,
    BREADBOARD_QC_CASE_ID: testCase.id,
    BREADBOARD_TUI_VIEWPORT_RESETS_FILE: viewportResetLogPath,
    BREADBOARD_TUI_SURFACE_MODEL_FILE: surfaceModelLogPath,
    BREADBOARD_TUI_SCROLLBACK_FEED_FILE: scrollbackFeedLogPath,
    BREADBOARD_TUI_MARKDOWN_METRICS_FILE: markdownMetricsLogPath,
    BREADBOARD_TUI_RENDER_TIMELINE_FILE: renderTimelineLogPath,
    BREADBOARD_TUI_APP_START_ANCHOR_FILE: appStartAnchorPath,
    BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE: managedRegionBoundsLogPath,
    BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE: scrollbackClauseVerdictsPath,
    ...envOverrides,
  }

  const configPath = resolveConfigPath(testCase, options)
  const baseUrl = testCase.baseUrl ?? options.baseUrl
  const command = testCase.command ?? options.command
  const commandCwd = resolveCaseCommandCwd(testCase, command)
  await fs.mkdir(commandCwd, { recursive: true })
  await writeCaseWorkspaceFiles(commandCwd, testCase.workspaceFiles)
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "codex_v1"
  const commandProvenance = await buildCommandProvenance(command, commandCwd)
  const terminalTextDir = path.join(caseDir, "terminal_text")
  await fs.mkdir(terminalTextDir, { recursive: true })

  const repro = buildReproInfo(
    {
      cwd: REPRO_CWD,
      env: {
        BREADBOARD_API_URL: baseUrl,
        BREADBOARD_STATE_DUMP_PATH: stateDumpPath,
        BREADBOARD_STATE_DUMP_MODE: "summary",
        BREADBOARD_STATE_DUMP_RATE_MS: "100",
        ...harnessEnvOverrides,
      },
      argv: [
        "pnpm",
        "exec",
        "tsx",
        "scripts/run_stress_bundles.ts",
        "--case",
        testCase.id,
        "--out-dir",
        toRepoRootPath(path.dirname(caseDir)),
      ],
    },
    undefined,
  )

  await fs.writeFile(
    caseInfoPath,
    `${JSON.stringify(
      {
        id: testCase.id,
        kind: testCase.kind,
        terminalLane: testCase.terminalLane,
        description: testCase.description,
        script: testCase.script,
        scriptHash,
        repro,
        config: configPath,
        baseUrl,
        command,
        commandCwd,
        commandProvenance,
        env: harnessEnvOverrides,
        chaos: chaosInfo,
        contractOverrides: options.contractOverrides,
        phase: testCase.terminalLane === "wezterm" ? "P4-B" : testCase.terminalLane === "ghostty" ? "P4-C" : "P4-A",
      },
      null,
      2,
    )}
`,
    "utf8",
  )

  const snapshotsPath = path.join(caseDir, "terminal_snapshots.txt")
  const plainPath = path.join(caseDir, "terminal_plain.txt")
  const metadataPath = path.join(caseDir, "terminal_metadata.json")
  const eventsPath = path.join(caseDir, "terminal_events.ndjson")
  const manifestPath = path.join(caseDir, "terminal_manifest.json")
  const configOutPath = path.join(caseDir, "config.json")
  const anomaliesPath = path.join(caseDir, "anomalies.json")
  const contractReportPath = path.join(caseDir, "contract_report.json")
  const metricsPath = path.join(caseDir, "metrics.json")
  const visibleFinalPath = path.join(terminalTextDir, "visible_final.txt")
  const scrollbackFinalPath = path.join(terminalTextDir, "scrollback_final.txt")
  const ssePath = path.join(caseDir, "sse_events.txt")
  const eventsNdjsonPath = path.join(caseDir, "events.ndjson")
  const flamegraphPath = path.join(caseDir, "timeline_flamegraph.txt")
  const ttyDocPath = path.join(caseDir, "ttydoc.txt")
  const steps = await loadStepsFromFile(testCase.script)

  await fs.writeFile(
    configOutPath,
    JSON.stringify(
      {
        configPath,
        baseUrl,
        command,
        commandProvenance,
        profile,
        env: harnessEnvOverrides,
        contractOverrides: options.contractOverrides,
        terminalLane: testCase.terminalLane,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )

  let harnessFailure: Error | null = null
  let harnessResult: Awaited<ReturnType<typeof runTerminalAdapterHarness>>
  try {
    harnessResult = await runTerminalAdapterHarness({
      lane: testCase.terminalLane,
      steps,
      command,
      cwd: commandCwd,
      configPath,
      baseUrl,
      cols: testCase.cols ?? 120,
      rows: testCase.rows ?? 36,
      envOverrides: harnessEnvOverrides,
      runtimeDir: caseDir,
      captureScreenshots: true,
      qcBatchId,
      qcCaseId: testCase.id,
      stateDumpPath,
      stateDumpMode: "summary",
      stateDumpRateMs: 100,
      maxDurationMs: REPL_SCRIPT_MAX_DURATION_MS,
    })
  } catch (error) {
    if (error instanceof TerminalAdapterHarnessError) {
      harnessResult = error.result
      harnessFailure = error
    } else {
      throw error
    }
  }

  await writeSnapshotFile(harnessResult.snapshots, snapshotsPath)
  await fs.writeFile(plainPath, harnessResult.plainBuffer, "utf8")
  await fs.writeFile(metadataPath, JSON.stringify(harnessResult.metadata, null, 2) + "\n", "utf8")
  const eventLines = harnessResult.stepEvents.map((event: TerminalAdapterStepEvent) => JSON.stringify(event)).join("\n")
  await fs.writeFile(eventsPath, eventLines.length > 0 ? `${eventLines}\n` : "", "utf8")
  await fs.writeFile(frameLogPath, harnessResult.frames.map((frame) => JSON.stringify(frame)).join("\n") + (harnessResult.frames.length > 0 ? "\n" : ""), "utf8")
  await fs.writeFile(inputLogPath, harnessResult.inputLogLines.length > 0 ? `${harnessResult.inputLogLines.join("\n")}\n` : "", "utf8")
  await fs.writeFile(visibleFinalPath, harnessResult.visibleTextFinal + (harnessResult.visibleTextFinal.endsWith("\n") ? "" : "\n"), "utf8")
  await fs.writeFile(scrollbackFinalPath, harnessResult.scrollbackTextFinal + (harnessResult.scrollbackTextFinal.endsWith("\n") ? "" : "\n"), "utf8")

  let contractReport: { summary?: Record<string, unknown>; errors?: unknown[]; warnings?: unknown[] }
  let metricsReport: Record<string, unknown>
  let timelinePaths: Awaited<ReturnType<typeof buildTimelineArtifacts>> = null
  const extraAnomalies = testCase.postCheckScript ? await runPostCheckScript(testCase.postCheckScript, caseDir) : []
  const finalScrollbackClauseVerdicts = buildFinalScrollbackClauseVerdicts({
    mode: testCase.env?.BREADBOARD_TUI_LIVE_SHELL_MODE ?? null,
    caseId: testCase.id,
    terminalLane: testCase.terminalLane,
    anomalies: extraAnomalies,
  })

  if (testCase.terminalLane === "wezterm") {
    const sessionId = await extractSessionIdFromStateDump(stateDumpPath)
    if (sessionId) {
      const sseHandle = tapSessionEvents(baseUrl, sessionId, ssePath, { replay: true, limit: 1000 })
      await Promise.race([
        sseHandle.promise,
        new Promise<void>((resolve) => {
          setTimeout(() => {
            sseHandle.stop()
            resolve()
          }, SSE_REPLAY_TIMEOUT_MS)
        }),
      ])
      sseHandle.stop()
      await sseHandle.promise.catch(() => undefined)
      if (!sseHandle.hasData()) {
        await writeFallbackMessage(ssePath, "[sse] Replay completed with no events.")
      }
    } else {
      await writeFallbackMessage(ssePath, "[sse] Session id not found in state dump.")
    }
    await writeEventsNdjson(ssePath, eventsNdjsonPath)
    contractReport = await runContractChecks(ssePath, options.contractOverrides ?? undefined)
    await fs.writeFile(contractReportPath, JSON.stringify(contractReport, null, 2), "utf8")
    await fs.writeFile(anomaliesPath, `${JSON.stringify(extraAnomalies, null, 2)}\n`, "utf8")
    await fs.writeFile(scrollbackClauseVerdictsPath, `${JSON.stringify(finalScrollbackClauseVerdicts, null, 2)}\n`, "utf8")
    timelinePaths = await buildTimelineArtifacts(caseDir, { chaosInfo, ssePath, anomaliesPath })
    if (!timelinePaths) {
      throw new Error(`[stress:${testCase.id}] Failed to build timeline artifacts in ${caseDir}`)
    }
    await buildFlamegraph(timelinePaths.timelinePath, flamegraphPath)
    await buildTtyDoc({
      caseDir,
      caseId: testCase.id,
      scriptPath: testCase.script,
      configPath,
    })
    metricsReport = await buildMetricsReport(timelinePaths.summaryPath, contractReport)
    metricsReport.terminalAdapter = {
      lane: testCase.terminalLane,
      targetIds: harnessResult.metadata.targetIds,
      capabilitySummary: harnessResult.metadata.capabilitySummary,
      stepCount: steps.length,
    }
  } else {
    contractReport = {
      summary: {
        mode: "terminal-adapter-dry-run",
        terminalLane: testCase.terminalLane,
        stepCount: steps.length,
        errorCount: 0,
        warningCount: 0,
      },
      errors: [],
      warnings: [],
    }
    await fs.writeFile(contractReportPath, JSON.stringify(contractReport, null, 2), "utf8")
    await fs.writeFile(anomaliesPath, `${JSON.stringify(extraAnomalies, null, 2)}\n`, "utf8")
    await fs.writeFile(scrollbackClauseVerdictsPath, `${JSON.stringify(finalScrollbackClauseVerdicts, null, 2)}\n`, "utf8")
    metricsReport = {
      timelineSummary: null,
      contract: contractReport.summary,
      contractIssues: { errors: 0, warnings: 0 },
      terminalAdapter: {
        lane: testCase.terminalLane,
        targetIds: harnessResult.metadata.targetIds,
        capabilitySummary: harnessResult.metadata.capabilitySummary,
        stepCount: steps.length,
      },
    }
  }

  await fs.writeFile(metricsPath, JSON.stringify(metricsReport, null, 2), "utf8")
  await fs.writeFile(
    manifestPath,
    JSON.stringify(
      {
        phase: testCase.terminalLane === "wezterm" ? "P4-B" : testCase.terminalLane === "ghostty" ? "P4-C" : "P4-A",
        lane: testCase.terminalLane,
        outputs: {
          snapshots: snapshotsPath,
          plain: plainPath,
          metadata: metadataPath,
          terminalEvents: eventsPath,
          frames: frameLogPath,
          inputLog: inputLogPath,
          visibleFinal: visibleFinalPath,
          scrollbackFinal: scrollbackFinalPath,
          config: configOutPath,
          replState: stateDumpPath,
          viewportResets: viewportResetLogPath,
          surfaceModel: surfaceModelLogPath,
          scrollbackFeed: scrollbackFeedLogPath,
          markdownMetrics: markdownMetricsLogPath,
          renderTimeline: renderTimelineLogPath,
          appStartAnchor: appStartAnchorPath,
          managedRegionBounds: managedRegionBoundsLogPath,
          scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
          observerRuntimeDir: harnessResult.artifactDirs?.runtimeDir ?? null,
          observerTextDir: harnessResult.artifactDirs?.textDir ?? null,
          observerPngDir: harnessResult.artifactDirs?.pngDir ?? null,
          sse: testCase.terminalLane === "wezterm" ? ssePath : null,
          sseEventsNdjson: testCase.terminalLane === "wezterm" ? eventsNdjsonPath : null,
          anomalies: anomaliesPath,
          timeline: timelinePaths?.timelinePath ?? null,
          timelineSummary: timelinePaths?.summaryPath ?? null,
          timelineFlamegraph: timelinePaths ? flamegraphPath : null,
          ttydoc: timelinePaths ? ttyDocPath : null,
          contractReport: contractReportPath,
          metrics: metricsPath,
          caseInfo: caseInfoPath,
        },
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )

  if (harnessFailure) {
    throw harnessFailure
  }
  await validateTerminalArtifacts(caseDir, testCase.id, testCase.terminalLane)

  return {
    manifestPath,
    metadataPath,
    terminalEventsPath: eventsPath,
    visibleFinalPath,
    scrollbackFinalPath,
    configPath: configOutPath,
    anomaliesPath,
    contractReportPath,
    metricsPath,
    inputLogPath,
    frameLogPath,
    stateDumpPath,
    eventsNdjsonPath: testCase.terminalLane === "wezterm" ? eventsNdjsonPath : null,
    timelinePath: timelinePaths?.timelinePath ?? null,
    timelineSummaryPath: timelinePaths?.summaryPath ?? null,
    timelineFlamegraphPath: timelinePaths ? flamegraphPath : null,
    ttyDocPath: timelinePaths ? ttyDocPath : null,
    commandProvenance,
    chaosInfo,
    terminalLane: testCase.terminalLane,
    terminalTargetIds: harnessResult.metadata.targetIds,
    outputs: {
      snapshots: snapshotsPath,
      plain: plainPath,
      metadata: metadataPath,
      terminalEvents: eventsPath,
      frames: frameLogPath,
      inputLog: inputLogPath,
      visibleFinal: visibleFinalPath,
      scrollbackFinal: scrollbackFinalPath,
      config: configOutPath,
      replState: stateDumpPath,
      viewportResets: viewportResetLogPath,
      surfaceModel: surfaceModelLogPath,
      scrollbackFeed: scrollbackFeedLogPath,
          markdownMetrics: markdownMetricsLogPath,
      renderTimeline: renderTimelineLogPath,
      observerRuntimeDir: harnessResult.artifactDirs?.runtimeDir ?? null,
      observerTextDir: harnessResult.artifactDirs?.textDir ?? null,
      observerPngDir: harnessResult.artifactDirs?.pngDir ?? null,
      sse: testCase.terminalLane === "wezterm" ? ssePath : null,
      sseEventsNdjson: testCase.terminalLane === "wezterm" ? eventsNdjsonPath : null,
      anomalies: anomaliesPath,
      timeline: timelinePaths?.timelinePath ?? null,
      timelineSummary: timelinePaths?.summaryPath ?? null,
      timelineFlamegraph: timelinePaths ? flamegraphPath : null,
      ttydoc: timelinePaths ? ttyDocPath : null,
      contractReport: contractReportPath,
      metrics: metricsPath,
      caseInfo: caseInfoPath,
    },
  }
}

const runEmulatorCase = async (
  testCase: EmulatorCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
): Promise<Record<string, unknown>> => {
  const copiedScript = await copyScriptFile(testCase.script, caseDir)
  const scriptHash = await computeScriptHash(copiedScript.source)
  const reproScriptPath = toRepoRootPath(copiedScript.destination)
  const caseInfoPath = path.join(caseDir, "case_info.json")
  const inputLogPath = path.join(caseDir, "input_log.ndjson")
  const stateDumpPath = path.join(caseDir, "repl_state.ndjson")
  const viewportResetLogPath = path.join(caseDir, "viewport_resets.ndjson")
  const surfaceModelLogPath = path.join(caseDir, "surface_model.ndjson")
  const scrollbackFeedLogPath = path.join(caseDir, "scrollback_feed.ndjson")
  const markdownMetricsLogPath = path.join(caseDir, "markdown_metrics.ndjson")
  const renderTimelineLogPath = path.join(caseDir, "render_timeline.ndjson")
  const appStartAnchorPath = path.join(caseDir, "app_start_anchor.txt")
  const managedRegionBoundsLogPath = path.join(caseDir, "managed_region_bounds.ndjson")
  const scrollbackClauseVerdictsPath = path.join(caseDir, "scrollback_clause_verdicts.json")
  await fs.writeFile(stateDumpPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(viewportResetLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(surfaceModelLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(scrollbackFeedLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(markdownMetricsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(renderTimelineLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(appStartAnchorPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(managedRegionBoundsLogPath, "", "utf8").catch(() => undefined)
  await fs.writeFile(
    scrollbackClauseVerdictsPath,
    `${JSON.stringify(buildInitialScrollbackClauseVerdicts(testCase.env?.BREADBOARD_TUI_LIVE_SHELL_MODE ?? null), null, 2)}\n`,
    "utf8",
  ).catch(() => undefined)
  const envOverrides = { ...(testCase.env ?? {}) }
  const qcBatchId = path.basename(path.dirname(caseDir))
  const harnessEnvOverrides = {
    BREADBOARD_QC_BATCH_ID: qcBatchId,
    BREADBOARD_QC_CASE_ID: testCase.id,
    BREADBOARD_TUI_VIEWPORT_RESETS_FILE: viewportResetLogPath,
    BREADBOARD_TUI_SURFACE_MODEL_FILE: surfaceModelLogPath,
    BREADBOARD_TUI_SCROLLBACK_FEED_FILE: scrollbackFeedLogPath,
    BREADBOARD_TUI_MARKDOWN_METRICS_FILE: markdownMetricsLogPath,
    BREADBOARD_TUI_RENDER_TIMELINE_FILE: renderTimelineLogPath,
    BREADBOARD_TUI_APP_START_ANCHOR_FILE: appStartAnchorPath,
    BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE: managedRegionBoundsLogPath,
    BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE: scrollbackClauseVerdictsPath,
    ...envOverrides,
  }
  const command = testCase.command ?? options.command
  const commandCwd = resolveCaseCommandCwd(testCase, command)
  await fs.mkdir(commandCwd, { recursive: true })
  const commandProvenance = await buildCommandProvenance(command, commandCwd)
  const configPath = resolveConfigPath(testCase, options)
  const baseUrl = testCase.baseUrl ?? options.baseUrl
  const repro = buildReproInfo(
    {
      cwd: REPRO_CWD,
      env: {
        BREADBOARD_API_URL: baseUrl,
        BREADBOARD_STATE_DUMP_PATH: stateDumpPath,
        BREADBOARD_STATE_DUMP_MODE: "summary",
        BREADBOARD_STATE_DUMP_RATE_MS: "100",
        ...harnessEnvOverrides,
      },
      argv: [
        "npx",
        "tsx",
        "scripts/harness/weztermObserver.ts",
        "--script",
        reproScriptPath,
        "--config",
        configPath,
        "--base-url",
        baseUrl,
        "--runtime-dir",
        toRepoRootPath(caseDir),
        "--snapshots",
        toRepoRootPath(path.join(caseDir, "emulator_snapshots.txt")),
        "--metadata",
        toRepoRootPath(path.join(caseDir, "emulator_metadata.json")),
        "--frames",
        toRepoRootPath(path.join(caseDir, "emulator_frames.ndjson")),
        "--input-log",
        toRepoRootPath(inputLogPath),
        "--state-dump",
        toRepoRootPath(stateDumpPath),
        "--cols",
        String(testCase.cols ?? 120),
        "--rows",
        String(testCase.rows ?? 36),
        "--cmd",
        command,
        "--cwd",
        commandCwd,
      ],
    },
    undefined,
  )
  await fs.writeFile(
    caseInfoPath,
    `${JSON.stringify(
      {
        id: testCase.id,
        kind: testCase.kind,
        description: testCase.description,
        script: testCase.script,
        scriptHash,
        repro,
        config: configPath,
        chaos: chaosInfo,
        env: envOverrides,
        contractOverrides: options.contractOverrides,
      },
      null,
      2,
    )}
`,
    "utf8",
  )

  const snapshotsPath = path.join(caseDir, "emulator_snapshots.txt")
  const plainPath = path.join(caseDir, "emulator_plain.txt")
  const metadataPath = path.join(caseDir, "emulator_metadata.json")
  const frameLogPath = path.join(caseDir, "emulator_frames.ndjson")
  const manifestPath = path.join(caseDir, "emulator_manifest.json")
  const configOutPath = path.join(caseDir, "config.json")
  const anomaliesPath = path.join(caseDir, "anomalies.json")
  const ssePath = path.join(caseDir, "sse_events.txt")
  const eventsNdjsonPath = path.join(caseDir, "events.ndjson")
  const contractReportPath = path.join(caseDir, "contract_report.json")
  const metricsPath = path.join(caseDir, "metrics.json")
  const flamegraphPath = path.join(caseDir, "timeline_flamegraph.txt")
  const ttyDocPath = path.join(caseDir, "ttydoc.txt")
  const observerTextDir = path.join(caseDir, "observer_text")
  const observerPngDir = path.join(caseDir, "observer_png")

  const steps = await loadStepsFromFile(testCase.script)
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "codex_v1"
  await fs.writeFile(
    configOutPath,
    JSON.stringify(
      {
        configPath,
        baseUrl,
        command,
        commandProvenance,
        profile,
        env: harnessEnvOverrides,
        contractOverrides: options.contractOverrides,
        observer: "wezterm",
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )

  const inputLog: string[] = []
  let harnessFailure: Error | null = null
  let harnessResult: Awaited<ReturnType<typeof runWeztermObserverHarness>>
  try {
    harnessResult = await runWeztermObserverHarness({
      steps,
      command,
      cwd: commandCwd,
      configPath,
      baseUrl,
      envOverrides: harnessEnvOverrides,
      cols: testCase.cols ?? 120,
      rows: testCase.rows ?? 36,
      onInput: (entry) => inputLog.push(JSON.stringify(entry)),
      stateDumpPath,
      stateDumpMode: "summary",
      stateDumpRateMs: 100,
      runtimeDir: caseDir,
      captureScreenshots: true,
    })
  } catch (error) {
    if (error instanceof EmulatorObserverHarnessError) {
      harnessResult = error.result
      harnessFailure = error
    } else {
      throw error
    }
  }

  await writeSnapshotFile(harnessResult.snapshots, snapshotsPath)
  await fs.writeFile(plainPath, harnessResult.plainBuffer, "utf8")
  await fs.writeFile(metadataPath, JSON.stringify(harnessResult.metadata, null, 2) + "\n", "utf8")
  await fs.writeFile(frameLogPath, harnessResult.frames.map((frame) => JSON.stringify(frame)).join("\n") + "\n", "utf8")
  await fs.writeFile(inputLogPath, inputLog.length > 0 ? `${inputLog.join("\n")}\n` : "", "utf8")

  const sessionId = await extractSessionIdFromStateDump(stateDumpPath)
  if (sessionId) {
    const sseHandle = tapSessionEvents(baseUrl, sessionId, ssePath, { replay: true, limit: 1000 })
    await Promise.race([
      sseHandle.promise,
      new Promise<void>((resolve) => {
        setTimeout(() => {
          sseHandle.stop()
          resolve()
        }, SSE_REPLAY_TIMEOUT_MS)
      }),
    ])
    sseHandle.stop()
    await sseHandle.promise.catch(() => undefined)
    if (!sseHandle.hasData()) {
      await writeFallbackMessage(ssePath, "[sse] Replay completed with no events.")
    }
  } else {
    await writeFallbackMessage(ssePath, "[sse] Session id not found in state dump.")
  }
  await writeEventsNdjson(ssePath, eventsNdjsonPath)
  const contractReport = await runContractChecks(ssePath, options.contractOverrides ?? undefined)
  await fs.writeFile(contractReportPath, JSON.stringify(contractReport, null, 2), "utf8")

  const extraAnomalies = testCase.postCheckScript
    ? await runPostCheckScript(testCase.postCheckScript, caseDir)
    : []
  await fs.writeFile(anomaliesPath, `${JSON.stringify(extraAnomalies, null, 2)}\n`, "utf8")

  const timelinePaths = await buildTimelineArtifacts(caseDir, { chaosInfo, ssePath, anomaliesPath })
  if (!timelinePaths) {
    throw new Error(`[stress:${testCase.id}] Failed to build timeline artifacts in ${caseDir}`)
  }
  await buildFlamegraph(timelinePaths.timelinePath, flamegraphPath)
  await buildTtyDoc({
    caseDir,
    caseId: testCase.id,
    scriptPath: testCase.script,
    configPath: options.configPath,
  })

  const metricsReport = await buildMetricsReport(timelinePaths.summaryPath, contractReport)
  await fs.writeFile(metricsPath, JSON.stringify(metricsReport, null, 2), "utf8")
  await fs.writeFile(
    manifestPath,
    JSON.stringify(
      {
        script: testCase.script,
        observer: "wezterm",
        outputs: {
          snapshots: snapshotsPath,
          plain: plainPath,
          metadata: metadataPath,
          frames: frameLogPath,
          config: configOutPath,
          inputLog: inputLogPath,
          replState: stateDumpPath,
          viewportResets: viewportResetLogPath,
          surfaceModel: surfaceModelLogPath,
          scrollbackFeed: scrollbackFeedLogPath,
          markdownMetrics: markdownMetricsLogPath,
          renderTimeline: renderTimelineLogPath,
          appStartAnchor: appStartAnchorPath,
          managedRegionBounds: managedRegionBoundsLogPath,
          scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
          observerTextDir,
          observerPngDir,
          anomalies: anomaliesPath,
          timeline: timelinePaths.timelinePath,
          timelineSummary: timelinePaths.summaryPath,
          timelineFlamegraph: flamegraphPath,
          ttydoc: ttyDocPath,
          sse: ssePath,
          sseEventsNdjson: eventsNdjsonPath,
          metrics: metricsPath,
          contractReport: contractReportPath,
        },
      },
      null,
      2,
    ) + "\n",
    "utf8",
  )

  if (harnessFailure) {
    throw harnessFailure
  }
  await validateEmulatorArtifacts(caseDir, testCase.id)

  return {
    snapshotsPath,
    plainPath,
    metadataPath,
    frameLogPath,
    inputLogPath,
    stateDumpPath,
    manifestPath,
    metricsPath,
    contractReportPath,
    eventsNdjsonPath,
    configPath: configOutPath,
    anomaliesPath,
    timelinePath: timelinePaths.timelinePath,
    timelineSummaryPath: timelinePaths.summaryPath,
    timelineFlamegraphPath: flamegraphPath,
    ttyDocPath,
    chaosInfo,
    commandProvenance,
    outputs: {
      snapshots: snapshotsPath,
      plain: plainPath,
      metadata: metadataPath,
      frames: frameLogPath,
      config: configOutPath,
      inputLog: inputLogPath,
      replState: stateDumpPath,
      viewportResets: viewportResetLogPath,
      surfaceModel: surfaceModelLogPath,
      scrollbackFeed: scrollbackFeedLogPath,
          markdownMetrics: markdownMetricsLogPath,
      renderTimeline: renderTimelineLogPath,
      appStartAnchor: appStartAnchorPath,
      managedRegionBounds: managedRegionBoundsLogPath,
      scrollbackClauseVerdicts: scrollbackClauseVerdictsPath,
      observerTextDir,
      observerPngDir,
      anomalies: anomaliesPath,
      timeline: timelinePaths.timelinePath,
      timelineSummary: timelinePaths.summaryPath,
      timelineFlamegraph: flamegraphPath,
      ttydoc: ttyDocPath,
      sse: ssePath,
      sseEventsNdjson: eventsNdjsonPath,
      metrics: metricsPath,
      contractReport: contractReportPath,
      caseInfo: caseInfoPath,
    },
  }
}

const runCaseForOptions = async (
  testCase: StressCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
) => {
  if (testCase.kind === "repl") {
    return runReplCase(testCase, options, caseDir, chaosInfo)
  }
  if (testCase.kind === "emulator") {
    return runEmulatorCase(testCase, options, caseDir, chaosInfo)
  }
  if (testCase.kind === "terminal") {
    return runTerminalCase(testCase, options, caseDir, chaosInfo)
  }
  return runPtyCase(testCase, options, caseDir, chaosInfo)
}

const runCaseWithOptionalMockSse = async (
  testCase: StressCase,
  options: CliOptions,
  caseDir: string,
): Promise<Record<string, unknown>> => {
  const applyMockEnv = (caseInput: StressCase): StressCase => {
    const nextEnv: Record<string, string> = {
      ...(caseInput.env ?? {}),
    }
    if (!nextEnv.BREADBOARD_ENGINE_MODE) {
      nextEnv.BREADBOARD_ENGINE_MODE = "external"
    }
    if (!nextEnv.MOCK_API_KEY) {
      nextEnv.MOCK_API_KEY = "breadboard-mock-key"
    }
    return {
      ...caseInput,
      env: nextEnv,
    }
  }
  const baseChaos = mergeChaosInfo(options.bridgeChaos, options.chaosInfo)
  if (options.mockSseConfig) {
    return runCaseForOptions(applyMockEnv(testCase), options, caseDir, baseChaos)
  }
  if (!options.useCaseMockSse || !testCase.mockSseScript) {
    return runCaseForOptions(testCase, options, caseDir, baseChaos)
  }
  const resolvedScript = path.isAbsolute(testCase.mockSseScript)
    ? testCase.mockSseScript
    : path.join(ROOT_DIR, testCase.mockSseScript)
  const config: MockSseOptions = {
    script: resolvedScript,
    ...options.mockSseDefaults,
  }
  const mockProcess = await startMockSseServer(config)
  const patchedOptions: CliOptions = {
    ...options,
    baseUrl: `http://${config.host}:${config.port}`,
  }
  const patchedCase = applyMockEnv(testCase)
  const chaosInfo: Record<string, unknown> = {
    mode: "mock-sse",
    origin: "case",
    caseId: testCase.id,
    script: config.script,
    port: config.port,
    loop: config.loop,
    delayMultiplier: config.delayMultiplier,
    jitterMs: config.jitterMs,
    dropRate: config.dropRate,
  }
  try {
    const mergedChaos = mergeChaosInfo(options.bridgeChaos, chaosInfo)
    return await runCaseForOptions(patchedCase, patchedOptions, caseDir, mergedChaos)
  } finally {
    await mockProcess.stop().catch((error) => console.warn("[stress] mock SSE shutdown failed:", error))
  }
}

const summarizeKeyFuzz = async (fuzzRoot: string) => {
  try {
    const entries = await fs.readdir(fuzzRoot, { withFileTypes: true })
    const dirs = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name)
    if (dirs.length === 0) return null
    const latest = dirs.sort().pop()!
    const runDir = path.join(fuzzRoot, latest)
    const runPath = path.join(runDir, "run.json")
    const run = JSON.parse(await fs.readFile(runPath, "utf8")) as Record<string, unknown>
    const failureEntries = (await fs.readdir(runDir, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("failure-"))
      .map((entry) => path.join(runDir, entry.name))
    const aggregate = (run.aggregate ?? null) as Record<string, unknown> | null
    const summary = {
      runDir,
      iterationsRequested: run.requestedIterations ?? run.iterations ?? null,
      iterationsCompleted: aggregate?.iterations ?? run.iterations ?? null,
      stepsPerIteration: run.stepsPerIteration ?? null,
      seed: run.seed ?? null,
      maxLength: aggregate?.maxLength ?? null,
      maxTypedChars: aggregate?.maxTypedChars ?? null,
      maxDeleteOps: aggregate?.maxDeleteOps ?? null,
      overDeleteAttempts: aggregate?.overDeleteAttempts ?? null,
      finalLengthRange: aggregate?.finalLengthRange ?? null,
      failures: failureEntries,
    }
    await fs.writeFile(path.join(fuzzRoot, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`, "utf8")
    return summary
  } catch (error) {
    console.warn(`[stress] key-fuzz summary failed: ${(error as Error).message}`)
    return null
  }
}

const runKeyFuzzSuite = async (batchDir: string, options: CliOptions) => {
  if (!options.keyFuzzIterations || options.keyFuzzIterations <= 0) {
    return null
  }
  const args = [
    "scripts/run_key_fuzz.ts",
    "--iterations",
    String(options.keyFuzzIterations),
    "--steps",
    String(options.keyFuzzSteps),
    "--artifact-dir",
    path.join(batchDir, "key_fuzz"),
    "--config",
    options.configPath,
    "--base-url",
    options.baseUrl,
  ]
  if (options.keyFuzzSeed != null) {
    args.push("--seed", String(options.keyFuzzSeed))
  }
  console.log(`[stress] running key-fuzz harness (iterations=${options.keyFuzzIterations}, steps=${options.keyFuzzSteps})`)
  await spawnLoggedProcess("tsx", args, { cwd: ROOT_DIR, env: process.env }, path.join(batchDir, "key_fuzz.log"))
  return summarizeKeyFuzz(path.join(batchDir, "key_fuzz"))
}

const zipDirectory = (sourceDir: string, targetZip: string) =>
  new Promise<void>((resolve, reject) => {
    const cwd = path.dirname(sourceDir)
    const dirName = path.basename(sourceDir)
    const child = spawn("zip", ["-r", targetZip, dirName], { cwd })
    child.on("close", (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`zip exited with code ${code}`))
      }
    })
    child.on("error", (error) => reject(error))
  })

const runGuardrailMetrics = async (batchDir: string, guardLogs: ReadonlyArray<string>) => {
  const scriptPath = path.resolve(ROOT_DIR, "..", "scripts", "ci_guardrail_metrics.sh")
  const outputPath = path.join(batchDir, "guardrail_metrics.jsonl")
  const env = {
    ...process.env,
    GUARDRAIL_METRICS_OUTPUT: outputPath,
  }
  const args = guardLogs.map((value) => path.resolve(value))
  await new Promise<void>((resolve, reject) => {
    const child = spawn("bash", [scriptPath, ...args], {
      cwd: path.resolve(ROOT_DIR, ".."),
      env,
      stdio: "inherit",
    })
    child.on("close", (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`guardrail metrics exited with code ${code}`))
      }
    })
    child.on("error", (error) => reject(error))
  })
  const summaryPath = `${outputPath.slice(0, -".jsonl".length)}.summary.json`
  const hasOutput = await fs
    .access(outputPath)
    .then(() => true)
    .catch(() => false)
  if (!hasOutput) {
    return null
  }
  return { outputPath, summaryPath }
}

const main = async () => {
  const options = parseCliArgs()
  let mockProcess: MockSseHandle | null = null
  let liveBridgeProcess: ChildProcess | null = null
  try {
    if (options.mockSseConfig) {
      mockProcess = await startMockSseServer(options.mockSseConfig)
      options.baseUrl = `http://${options.mockSseConfig.host}:${options.mockSseConfig.port}`
    }
    const timestamp = formatTimestamp()
    const batchDir = path.join(options.outDir, timestamp)
    await fs.mkdir(batchDir, { recursive: true })
    console.log(
      `[stress] starting bundle kind=${options.bundleKind} authority=${options.bundleAuthority} out=${batchDir}`,
    )

    const selectedCases = resolveCaseList(options.cases, options.includeLive)
    const needsDistBuild = selectedCases.some((entry) => {
      if (entry.kind === "pty" && entry.command) {
        return entry.command.includes("dist/main.js")
      }
      // repl cases and pty cases without an explicit command rely on the built TUI.
      return true
    })
    if (needsDistBuild) {
      await ensureDistBuild()
    }
    const needsLive = selectedCases.some((entry) => entry.requiresLive)
    if (needsLive) {
      if (options.freshLive) {
        await shutdownEngine({ timeoutMs: 5_000, force: true }).catch(() => undefined)
        await shutdownLiveBridge(options.baseUrl, 5_000).catch(() => undefined)
      }
      liveBridgeProcess = await ensureLiveBridge(options.baseUrl, options.autoStartLive)
    }
    if (!options.includeLive && !options.cases) {
      const skipped = STRESS_CASES.filter((entry) => entry.requiresLive)
      if (skipped.length > 0) {
        console.log(
          `[stress] skipping ${skipped.length} live engine case(s). Re-run with --include-live to enable.`,
        )
      }
    }
    const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "codex_v1"
    const gitInfo = await readGitInfo()
    const liveBridgeProvenance = needsLive ? await readLiveBridgeProvenance(options.baseUrl) : null
    const liveMode: "fresh-live" | "warm-live" | "not-live" = needsLive
      ? options.freshLive
        ? "fresh-live"
        : "warm-live"
      : "not-live"
    const liveProvenanceReport = evaluateLiveProvenance({
      liveMode,
      expected: {
        repoRoot: REPO_ROOT,
        commit: gitInfo?.commit ?? null,
        branch: gitInfo?.branch ?? null,
        dirty: gitInfo?.dirty ?? null,
      },
      observed: (liveBridgeProvenance ?? null) as any,
      mismatchOverride: options.allowRevisionMismatch,
    })
    const aggregateManifest: Record<string, unknown> = {
      schemaVersion: 3,
      qcSpineVersion: "p2_v1",
      eventSchemaVersion: "event_envelope_v1",
      bundleKind: options.bundleKind,
      bundleAuthority: options.bundleAuthority,
      liveMode,
      startedAt: Date.now(),
      startedAtIso: new Date().toISOString(),
      config: options.configPath,
      baseUrl: options.baseUrl,
      command: options.command,
      profile,
      runtime: {
        node: process.version,
        platform: process.platform,
        arch: process.arch,
      },
      terminal: {
        term: process.env.TERM ?? null,
        cols: process.stdout?.columns ?? null,
        rows: process.stdout?.rows ?? null,
      },
      provenance: {
        runner: {
          rootDir: ROOT_DIR,
          repoRoot: REPO_ROOT,
          cwd: process.cwd(),
          git: gitInfo,
        },
        liveEngine: liveBridgeProvenance
          ? {
              baseUrl: options.baseUrl,
              autoStarted: Boolean(liveBridgeProcess),
              freshLive: options.freshLive,
              ...liveBridgeProvenance,
            }
          : null,
      },
      cases: [] as Array<Record<string, unknown>>,
    }

    if (gitInfo) {
      aggregateManifest.git = gitInfo
    }
    aggregateManifest.liveProvenance = liveProvenanceReport.summary
    const liveProvenanceReportPath = path.join(batchDir, "live_provenance_report.json")
    await fs.writeFile(liveProvenanceReportPath, JSON.stringify(liveProvenanceReport, null, 2), "utf8")
    aggregateManifest.liveProvenanceReport = path.relative(batchDir, liveProvenanceReportPath)
    if (liveProvenanceReport.warnings.length > 0) {
      console.warn(
        `[stress] live provenance warnings: ${liveProvenanceReport.warnings.map((entry) => entry.id).join(", ")}`,
      )
    }
    if (liveProvenanceReport.errors.length > 0 && !options.allowRevisionMismatch) {
      throw new Error(
        `live provenance failed with ${liveProvenanceReport.errors.length} error(s); see ${liveProvenanceReportPath}`,
      )
    }
    if (liveProvenanceReport.errors.length > 0 && options.allowRevisionMismatch) {
      console.warn(
        `[stress] live provenance mismatch override active: ${liveProvenanceReport.errors.map((entry) => entry.id).join(", ")}`,
      )
    }

    for (const testCase of selectedCases) {
      const caseDir = path.join(batchDir, testCase.id)
      await fs.mkdir(caseDir, { recursive: true })
      const summary: Record<string, unknown> = {
        id: testCase.id,
        kind: testCase.kind,
        description: testCase.description,
        script: testCase.script,
        requiresLive: Boolean(testCase.requiresLive),
      }
      try {
        const stats = await runCaseWithOptionalMockSse(testCase, options, caseDir)
        Object.assign(summary, stats)
        if ((stats as Record<string, unknown>).chaosInfo !== undefined) {
          summary.chaos = (stats as Record<string, unknown>).chaosInfo
        }
        const clipboardCopy = await copyClipboardManifest(
          (stats as Record<string, unknown>).manifestPath as string | undefined,
          testCase.id,
          batchDir,
        )
        if (clipboardCopy) {
          summary.clipboardManifestPath = path.relative(batchDir, clipboardCopy)
        }
        if ((stats as Record<string, unknown>).outputs) {
          summary.outputs = (stats as Record<string, unknown>).outputs
        }
        const anomaliesPath = (stats as Record<string, unknown>).anomaliesPath as string | undefined
        if (anomaliesPath) {
          const anomalies = await readJsonFile<unknown[]>(anomaliesPath)
          if (Array.isArray(anomalies)) {
            summary.anomalyCount = anomalies.length
            if (anomalies.length > 0) {
              summary.anomalyIds = anomalies
                .map((entry) =>
                  entry && typeof entry === "object" && "id" in entry ? String((entry as { id?: unknown }).id) : "unknown",
                )
                .slice(0, 20)
            }
          }
        }
        ;(aggregateManifest.cases as Array<Record<string, unknown>>).push(summary)
        console.log(`[stress] ${testCase.id} captured.`)
      } catch (error) {
        console.error(`[stress] ${testCase.id} failed: ${(error as Error).message}`)
        throw error
      }
    }

    aggregateManifest.finishedAt = Date.now()
    aggregateManifest.finishedAtIso = new Date().toISOString()
    aggregateManifest.artifactDir = batchDir
    if (options.bridgeChaos) {
      aggregateManifest.bridgeChaos = options.bridgeChaos
    }
    let keyFuzzSummary: Record<string, unknown> | null = null
    if (options.keyFuzzIterations > 0) {
      const fuzzSummary = await runKeyFuzzSuite(batchDir, options)
      keyFuzzSummary = fuzzSummary
      aggregateManifest.keyFuzz = {
        iterations: options.keyFuzzIterations,
        steps: options.keyFuzzSteps,
        seed: options.keyFuzzSeed,
        artifactDir: path.join(batchDir, "key_fuzz"),
        summary: fuzzSummary,
      }
    }
    const clipboardDiffs = await compareClipboardAgainstPrevious(
      batchDir,
      options.outDir,
      aggregateManifest.cases as Array<Record<string, unknown>>,
    )
    if (clipboardDiffs.length > 0) {
      aggregateManifest.clipboardDiffs = clipboardDiffs
      console.warn(`[stress] clipboard metadata changed: ${clipboardDiffs.map((entry) => entry.caseId).join(", ")}`)
    }
    await buildClipboardDiffReport(batchDir).catch((error) =>
      console.warn(`[stress] clipboard diff report failed: ${(error as Error).message}`),
    )
    if (keyFuzzSummary) {
      await buildKeyFuzzReport(batchDir, keyFuzzSummary as any).catch((error) =>
        console.warn(`[stress] key-fuzz report failed: ${(error as Error).message}`),
      )
    }
    let guardMetricsResult: { outputPath: string; summaryPath: string } | null = null
    if (!options.skipGuardMetrics) {
      try {
        const metrics = await runGuardrailMetrics(batchDir, options.guardLogs)
        if (metrics) {
          guardMetricsResult = metrics
          aggregateManifest.guardrailMetrics = metrics
        }
      } catch (error) {
        console.error(`[stress] Guardrail metrics failed: ${(error as Error).message}`)
      }
    }

    if (guardMetricsResult) {
      const summaryPath = guardMetricsResult.summaryPath
      for (const caseSummary of aggregateManifest.cases as Array<Record<string, any>>) {
        const caseId = caseSummary.id as string
        const caseDir = path.join(batchDir, caseId)
        const guardDest = path.join(caseDir, "guardrail_summary.json")
        await fs.copyFile(summaryPath, guardDest).catch(() => undefined)
        caseSummary.guardrailSummaryPath = path.relative(batchDir, guardDest)
        await buildTtyDoc({
          caseDir,
          caseId,
          scriptPath: caseSummary.script as string,
          configPath: options.configPath,
          guardSummaryPath: guardDest,
        })
      }
    }

    const manifestHashes = await buildManifestHashes(
      batchDir,
      aggregateManifest.cases as Array<Record<string, unknown>>,
    ).catch(() => null)
    if (manifestHashes) {
      aggregateManifest.artifactHashes = manifestHashes
    }

    await buildBatchTtyDoc(batchDir).catch((error) =>
      console.warn(`[stress] batch ttydoc report failed: ${(error as Error).message}`),
    )

    if (options.tmuxCaptureTarget) {
      try {
        const capture = await captureTmuxPane(options.tmuxCaptureTarget, batchDir, options.tmuxCaptureScale)
        if (capture) {
          aggregateManifest.tmuxCapture = {
            target: options.tmuxCaptureTarget,
            png: path.relative(batchDir, capture.png),
            ansi: path.relative(batchDir, capture.ansi),
            text: path.relative(batchDir, capture.text),
          }
        }
      } catch (error) {
        console.warn(`[stress] tmux capture failed: ${(error as Error).message}`)
      }
    }

    const manifestPath = path.join(batchDir, "manifest.json")
    await fs.writeFile(manifestPath, JSON.stringify(aggregateManifest, null, 2), "utf8")

    const batchContractReport = evaluateBatchManifestSchema(aggregateManifest)
    const batchContractReportPath = path.join(batchDir, "batch_contract_report.json")
    await fs.writeFile(batchContractReportPath, JSON.stringify(batchContractReport, null, 2), "utf8")
    if (batchContractReport.errors.length > 0) {
      throw new Error(`batch manifest contract failed with ${batchContractReport.errors.length} error(s); see ${batchContractReportPath}`)
    }

    if (options.zip) {
      const zipName = `${path.basename(batchDir)}.zip`
      const zipPath = path.join(path.dirname(batchDir), zipName)
      await zipDirectory(batchDir, zipName)
      console.log(`[stress] Bundle zipped at ${zipPath}`)
    } else {
      console.log(
        `[stress] bundle complete kind=${options.bundleKind} authority=${options.bundleAuthority} available=${batchDir}`,
      )
    }
    const keepRuns = Number(process.env.STRESS_KEEP_RUNS ?? DEFAULT_KEEP_RUNS)
    await pruneOldBatches(options.outDir, keepRuns).catch((error) =>
      console.warn(`[stress] prune failed: ${(error as Error).message}`),
    )
  } finally {
    if (mockProcess) {
      await mockProcess.stop().catch((error) => console.warn("[stress] mock SSE shutdown failed:", error))
    }
    if (liveBridgeProcess && !liveBridgeProcess.killed) {
      try {
        liveBridgeProcess.kill("SIGTERM")
      } catch {
        // ignore
      }
    }
  }
}

main()
  .then(() => {
    // This is a CLI harness: once artifacts are written and explicit cleanup has run,
    // do not let imported terminal/runtime libraries or tsx services pin the event loop.
    process.exit(0)
  })
  .catch((error) => {
    console.error("stress:bundle failed:", error)
    process.exit(1)
  })
