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
} from "./harness/spectator.js"
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

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const ROOT_DIR = path.resolve(__dirname, "..")
const REPRO_CWD = path.basename(ROOT_DIR)
const SSE_WAIT_TIMEOUT_MS = 45_000
const SSE_REPLAY_TIMEOUT_MS = 4_000
const DEFAULT_KEEP_RUNS = 3

const DEFAULT_CONFIG = process.env.CONFIG_PATH ?? process.env.STRESS_CONFIG ?? "../agent_configs/opencode_cli_mock_guardrails.yaml"
const DEFAULT_LIVE_CONFIG =
  process.env.BREADBOARD_LIVE_CONFIG || "../agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml"
const DEFAULT_BASE_URL = process.env.BASE_URL ?? process.env.BREADBOARD_API_URL ?? "http://127.0.0.1:9099"
const DEFAULT_COMMAND = "node dist/main.js repl"
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
    const child = spawn(
      "python",
      [scriptPath, "--target", target, "--out", outPath, "--scale", String(scale ?? 1)],
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
  readonly baseUrl?: string
  readonly requiresLive?: boolean
}

interface ReplCase extends BaseCase {
  readonly kind: "repl"
  readonly model?: string
}

interface PtyCase extends BaseCase {
  readonly kind: "pty"
  readonly clipboardText?: string
  readonly clipboardFile?: string
  readonly submitTimeoutMs?: number
  readonly clipboardAssertions?: ClipboardAssertions
  readonly winchScript?: string
  readonly expectedAltText?: string
}

type StressCase = ReplCase | PtyCase

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
    script: "scripts/network_error_pty.json",
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
    script: "scripts/streaming_markdown_pty.json",
    description: "Streaming markdown render updates",
    mockSseScript: "scripts/mock_sse_streaming_markdown.json",
    submitTimeoutMs: 0,
  },
  {
    id: "file_picker",
    kind: "pty",
    script: "scripts/file_picker_pty.json",
    description: "`@` file picker menu + mention insertion",
    mockSseScript: "scripts/mock_sse_file_picker.json",
    submitTimeoutMs: 0,
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
      BREADBOARD_TUI_CHROME: "claude",
      BREADBOARD_SHORTCUTS_STICKY: "1",
    },
  },
  {
    id: "transcript_viewer_toggle",
    kind: "pty",
    script: "scripts/transcript_viewer_toggle_pty.json",
    description: "Ctrl+T transcript viewer toggle (alt-screen)",
    mockSseScript: "scripts/mock_sse_sample.json",
    expectedAltText: "TOOLS:",
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
    id: "transcript_save",
    kind: "pty",
    script: "scripts/transcript_save_pty.json",
    description: "Transcript viewer save/export flow",
    mockSseScript: "scripts/mock_sse_sample.json",
    submitTimeoutMs: 0,
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
  },
  {
    id: "live_engine_smoke",
    kind: "pty",
    script: "scripts/live_engine_smoke_pty.json",
    description: "Live engine smoke (requires running CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_permission",
    kind: "pty",
    script: "scripts/live_engine_permission_pty.json",
    description: "Live engine permission modal flow (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
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
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_stop_retry",
    kind: "pty",
    script: "scripts/live_engine_stop_retry_pty.json",
    description: "Live engine stop + /retry semantics (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_tool_events",
    kind: "pty",
    script: "scripts/live_engine_tool_events_pty.json",
    description: "Live engine tool events (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_rewind",
    kind: "pty",
    script: "scripts/live_engine_rewind_pty.json",
    description: "Live engine rewind checkpoints (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_skills",
    kind: "pty",
    script: "scripts/live_engine_skills_pty.json",
    description: "Live engine skills picker (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
  },
  {
    id: "live_engine_ctree",
    kind: "pty",
    script: "scripts/live_engine_ctree_pty.json",
    description: "Live engine CTree summary (requires CLI bridge)",
    requiresLive: true,
    submitTimeoutMs: 0,
    configPath: "agent_configs/opencode_mock_c_fs.yaml",
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

  const absoluteOut = path.isAbsolute(outDir) ? outDir : path.resolve(ROOT_DIR, outDir)
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
        script: path.isAbsolute(mockSseScript) ? mockSseScript : path.join(process.cwd(), mockSseScript),
        ...mockDefaults,
      }
    : null

  return {
    cases: selected.length > 0 ? selected : null,
    configPath,
    liveConfigPath,
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
  if (testCase.requiresLive && options.liveConfigPath) {
    return options.liveConfigPath
  }
  return testCase.configPath ?? options.configPath
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
  const stateDumpRel = toRepoRootPath(stateDumpPath)
  const caseInfoPath = path.join(caseDir, "case_info.json")
  const envOverrides = testCase.env ?? {}
  const reproEnv = {
    BREADBOARD_API_URL: baseUrl,
    BREADBOARD_STATE_DUMP_PATH: stateDumpRel,
    BREADBOARD_STATE_DUMP_MODE: "summary",
    BREADBOARD_STATE_DUMP_RATE_MS: "100",
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
        config: options.configPath,
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
  const stateDumpEnvPath = stateDumpRel
  const args = [
    "dist/main.js",
    "repl",
    "--config",
    options.configPath,
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
    BREADBOARD_API_URL: options.baseUrl,
    BREADBOARD_STATE_DUMP_PATH: stateDumpEnvPath,
    BREADBOARD_STATE_DUMP_MODE: "summary",
    BREADBOARD_STATE_DUMP_RATE_MS: "100",
    ...envOverrides,
  }
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "claude_v1"
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
      sseHandle = tapSessionEvents(options.baseUrl, match[1], ssePath)
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
      timeline: timelinePaths.timelinePath,
      timelineSummary: timelinePaths.summaryPath,
      timelineFlamegraph: flamegraphPath,
      ttydoc: ttyDocPath,
      caseInfo: caseInfoPath,
    },
  }
  return stats
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
  const stateDumpRel = toRepoRootPath(stateDumpPath)
  const envOverrides = testCase.env ?? {}
  const command = testCase.command ?? options.command
  const configPath = resolveConfigPath(testCase, options)
  const baseUrl = testCase.baseUrl ?? options.baseUrl
  const repro = buildReproInfo(
    {
      cwd: REPRO_CWD,
      env: {
        BREADBOARD_API_URL: baseUrl,
        BREADBOARD_STATE_DUMP_PATH: stateDumpRel,
        BREADBOARD_STATE_DUMP_MODE: "summary",
        BREADBOARD_STATE_DUMP_RATE_MS: "100",
        ...envOverrides,
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
        "120",
        "--rows",
        "36",
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
        config: options.configPath,
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
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "claude_v1"
  await fs.writeFile(
    configOutPath,
    JSON.stringify(
      {
        configPath,
        baseUrl,
        command,
        profile,
        env: envOverrides,
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
      configPath,
      baseUrl,
      envOverrides,
      cols: 120,
      rows: 36,
      echo: false,
      clipboardPayload,
      submitTimeoutMs: testCase.submitTimeoutMs,
      winchEvents,
      attachmentSummaryPath,
      onInput: (entry) => inputLog.push(JSON.stringify(entry)),
      stateDumpPath: stateDumpRel,
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
    const useLiveCapture = chaosInfo?.mode === "mock-sse" || Boolean(options.mockSseConfig)
    const sseHandle = useLiveCapture
      ? tapSessionEvents(options.baseUrl, sessionId, ssePath)
      : tapSessionEvents(options.baseUrl, sessionId, ssePath, { replay: true, limit: 1000 })
    if (useLiveCapture) {
      await triggerMockPlayback(options.baseUrl, sessionId)
    }
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
      await writeFallbackMessage(
        ssePath,
        useLiveCapture ? "[sse] Live capture completed with no events." : "[sse] Replay completed with no events.",
      )
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
    outputs: {
      snapshots: snapshotsPath,
      raw: rawLogPath,
      plain: plainLogPath,
      metadata: metadataPath,
      config: configOutPath,
      frames: frameLogPath,
      inputLog: inputLogPath,
      replState: stateDumpPath,
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

const runCaseForOptions = async (
  testCase: StressCase,
  options: CliOptions,
  caseDir: string,
  chaosInfo: Record<string, unknown> | null,
) => {
  if (testCase.kind === "repl") {
    return runReplCase(testCase, options, caseDir, chaosInfo)
  }
  return runPtyCase(testCase, options, caseDir, chaosInfo)
}

const runCaseWithOptionalMockSse = async (
  testCase: StressCase,
  options: CliOptions,
  caseDir: string,
): Promise<Record<string, unknown>> => {
  const baseChaos = mergeChaosInfo(options.bridgeChaos, options.chaosInfo)
  if (options.mockSseConfig) {
    return runCaseForOptions(testCase, options, caseDir, baseChaos)
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
    return await runCaseForOptions(testCase, patchedOptions, caseDir, mergedChaos)
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
  await ensureDistBuild()
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

    const selectedCases = resolveCaseList(options.cases, options.includeLive)
    const needsLive = selectedCases.some((entry) => entry.requiresLive)
    if (needsLive) {
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
  const profile = process.env.BREADBOARD_TUI_PROFILE ?? process.env.BREADBOARD_PROFILE ?? "claude_v1"
  const aggregateManifest: Record<string, unknown> = {
    schemaVersion: 1,
    eventSchemaVersion: "event_envelope_v1",
    startedAt: Date.now(),
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
    cases: [] as Array<Record<string, unknown>>,
  }

  const gitInfo = await readGitInfo()
  if (gitInfo) {
    aggregateManifest.git = gitInfo
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
        ;(aggregateManifest.cases as Array<Record<string, unknown>>).push(summary)
        console.log(`[stress] ${testCase.id} captured.`)
      } catch (error) {
        console.error(`[stress] ${testCase.id} failed: ${(error as Error).message}`)
        throw error
      }
    }

  aggregateManifest.finishedAt = Date.now()
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

  const manifestPath = path.join(batchDir, "manifest.json")
  await fs.writeFile(manifestPath, JSON.stringify(aggregateManifest, null, 2), "utf8")

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

  if (options.zip) {
    const zipName = `${path.basename(batchDir)}.zip`
    const zipPath = path.join(path.dirname(batchDir), zipName)
    await zipDirectory(batchDir, zipName)
    console.log(`[stress] Bundle zipped at ${zipPath}`)
  } else {
    console.log(`[stress] Bundle available at ${batchDir}`)
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

main().catch((error) => {
  console.error("stress:bundle failed:", error)
  process.exitCode = 1
})
