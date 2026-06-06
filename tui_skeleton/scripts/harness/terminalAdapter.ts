import crypto from "node:crypto"
import path from "node:path"
import type { Step, SnapshotEntry } from "./spectator.ts"
import {
  EmulatorObserverHarnessError,
  runWeztermObserverHarness,
} from "./weztermObserver.ts"
import {
  GhosttyObserverHarnessError,
  runGhosttyObserverHarness,
} from "./ghosttyObserver.ts"

export type TerminalAdapterLane = "dry-run" | "wezterm" | "ghostty"

export interface TerminalAdapterTargetIds {
  readonly terminalSessionId: string
  readonly windowId: string
  readonly tabId: string
  readonly paneId: string
}

export interface TerminalAdapterCapabilitySummary {
  readonly launch: boolean
  readonly discoverTargets: boolean
  readonly selectTarget: boolean
  readonly injectText: boolean
  readonly injectKey: boolean
  readonly resizeRowsCols: boolean
  readonly resizeWindowPx: boolean
  readonly extractVisibleText: boolean
  readonly extractScrollbackText: boolean
  readonly screenshot: boolean
  readonly record: boolean
}

export interface TerminalAdapterStepEvent {
  readonly stepId: string
  readonly stepIndex: number
  readonly action: Step["action"]
  readonly label: string
  readonly t_rel_ms: number
  readonly t_wall_iso: string
  readonly targetIds: TerminalAdapterTargetIds
  readonly capturePolicy: "step-log" | "milestone" | "burst"
}

export interface TerminalAdapterFrame {
  readonly timestamp: number
  readonly label: string
  readonly text: string
  readonly screenshotPath?: string | null
}

export interface TerminalAdapterHarnessOptions {
  readonly lane: TerminalAdapterLane
  readonly steps: Step[]
  readonly command: string
  readonly cwd?: string
  readonly configPath?: string
  readonly baseUrl?: string
  readonly cols: number
  readonly rows: number
  readonly runtimeDir: string
  readonly envOverrides?: Record<string, string>
  readonly captureScreenshots?: boolean
  readonly qcBatchId?: string
  readonly qcCaseId?: string
  readonly stateDumpPath?: string
  readonly stateDumpMode?: "summary" | "full"
  readonly stateDumpRateMs?: number
  readonly maxDurationMs?: number
}

export interface TerminalAdapterHarnessResult {
  readonly snapshots: SnapshotEntry[]
  readonly plainBuffer: string
  readonly visibleTextFinal: string
  readonly scrollbackTextFinal: string
  readonly stepEvents: TerminalAdapterStepEvent[]
  readonly frames: TerminalAdapterFrame[]
  readonly inputLogLines: string[]
  readonly metadata: {
    readonly startedAt: number
    readonly finishedAt: number
    readonly durationMs: number
    readonly lane: TerminalAdapterLane
    readonly command: string
    readonly cols: number
    readonly rows: number
    readonly runtimeDir: string
    readonly targetIds: TerminalAdapterTargetIds
    readonly capabilitySummary: TerminalAdapterCapabilitySummary
    readonly integrationMode: "dry-run" | "wezterm" | "ghostty"
    readonly display?: string | null
    readonly socketPath?: string | null
    readonly x11WindowId?: string | null
  }
  readonly artifactDirs?: {
    readonly runtimeDir?: string
    readonly textDir?: string
    readonly pngDir?: string
  }
}

export class TerminalAdapterHarnessError extends Error {
  readonly result: TerminalAdapterHarnessResult

  constructor(message: string, result: TerminalAdapterHarnessResult, cause?: unknown) {
    super(message)
    this.name = "TerminalAdapterHarnessError"
    this.result = result
    ;(this as { cause?: unknown }).cause = cause
  }
}

const capabilitySummaryForLane = (lane: TerminalAdapterLane): TerminalAdapterCapabilitySummary => {
  if (lane === "wezterm") {
    return {
      launch: true,
      discoverTargets: true,
      selectTarget: true,
      injectText: true,
      injectKey: true,
      resizeRowsCols: true,
      resizeWindowPx: true,
      extractVisibleText: true,
      extractScrollbackText: true,
      screenshot: true,
      record: true,
    }
  }
  if (lane === "ghostty") {
    return {
      launch: true,
      discoverTargets: true,
      selectTarget: true,
      injectText: true,
      injectKey: true,
      resizeRowsCols: false,
      resizeWindowPx: true,
      extractVisibleText: true,
      extractScrollbackText: true,
      screenshot: true,
      record: false,
    }
  }
  return {
    launch: true,
    discoverTargets: true,
    selectTarget: true,
    injectText: true,
    injectKey: true,
    resizeRowsCols: true,
    resizeWindowPx: true,
    extractVisibleText: true,
    extractScrollbackText: true,
    screenshot: false,
    record: false,
  }
}

const describeStep = (step: Step): string => {
  switch (step.action) {
    case "wait":
      return `wait ${step.ms}ms`
    case "type":
      return `type ${JSON.stringify(step.text)}`
    case "paste":
      return `paste ${JSON.stringify(step.text)}`
    case "press":
      return `press ${step.key}${step.repeat ? ` x${step.repeat}` : ""}`
    case "snapshot":
      return `snapshot ${step.label}`
    case "snapshotBurst":
      return `snapshotBurst ${step.label} x${step.count}`
    case "waitFor":
      return `waitFor ${JSON.stringify(step.text)}`
    case "waitForComposerReady":
      return "waitForComposerReady"
    case "waitForState":
      return "waitForState"
    case "log":
      return `log ${step.message}`
    case "resize":
      return `resize ${step.cols}x${step.rows}`
    default:
      return String(step satisfies never)
  }
}

const capturePolicyForStep = (step: Step): TerminalAdapterStepEvent["capturePolicy"] => {
  if (step.action === "snapshot" || step.action === "waitFor" || step.action === "waitForComposerReady" || step.action === "waitForState") {
    return "milestone"
  }
  if (step.action === "snapshotBurst") {
    return "burst"
  }
  return "step-log"
}

const buildDeterministicTargetIds = (options: TerminalAdapterHarnessOptions): TerminalAdapterTargetIds => {
  const seed = crypto
    .createHash("sha1")
    .update(`${options.lane}\n${options.command}\n${options.runtimeDir}`)
    .digest("hex")
    .slice(0, 12)
  return {
    terminalSessionId: `${options.lane}-session-${seed}`,
    windowId: `${options.lane}-window-${seed.slice(0, 8)}`,
    tabId: `${options.lane}-tab-${seed.slice(2, 10)}`,
    paneId: `${options.lane}-pane-${seed.slice(4, 12)}`,
  }
}

const normalizeWeztermTargetIds = (options: TerminalAdapterHarnessOptions, entry: {
  readonly paneId: string
  readonly windowId?: string | null
  readonly tabId?: string | null
  readonly x11WindowId?: string | null
}): TerminalAdapterTargetIds => {
  const sessionSeed = crypto
    .createHash("sha1")
    .update(`${entry.paneId}\n${entry.windowId ?? ""}\n${entry.tabId ?? ""}\n${entry.x11WindowId ?? ""}\n${options.runtimeDir}`)
    .digest("hex")
    .slice(0, 12)
  return {
    terminalSessionId: `wezterm-session-${sessionSeed}`,
    windowId: entry.windowId?.trim() || entry.x11WindowId?.trim() || `wezterm-window-${sessionSeed.slice(0, 8)}`,
    tabId: entry.tabId?.trim() || `wezterm-tab-${sessionSeed.slice(2, 10)}`,
    paneId: entry.paneId.trim(),
  }
}

const runDryRunTerminalAdapterHarness = async (
  options: TerminalAdapterHarnessOptions,
): Promise<TerminalAdapterHarnessResult> => {
  const startedAt = Date.now()
  const targetIds = buildDeterministicTargetIds(options)
  const stepEvents: TerminalAdapterStepEvent[] = []
  const plainLines = [
    `terminal-adapter dry run`,
    `lane: ${options.lane}`,
    `command: ${options.command}`,
    `cwd: ${options.cwd ?? process.cwd()}`,
    `runtimeDir: ${path.resolve(options.runtimeDir)}`,
    `geometry: ${options.cols}x${options.rows}`,
    `configPath: ${options.configPath ?? ""}`,
    `baseUrl: ${options.baseUrl ?? ""}`,
    `steps: ${options.steps.length}`,
  ]
  const snapshots: SnapshotEntry[] = []
  for (const [stepIndex, step] of options.steps.entries()) {
    const t_rel_ms = stepIndex * 10
    const label = step.action === "snapshot" ? step.label : `step-${String(stepIndex + 1).padStart(2, "0")}-${step.action}`
    stepEvents.push({
      stepId: `${options.lane}-step-${String(stepIndex + 1).padStart(3, "0")}`,
      stepIndex,
      action: step.action,
      label,
      t_rel_ms,
      t_wall_iso: new Date(startedAt + t_rel_ms).toISOString(),
      targetIds,
      capturePolicy: capturePolicyForStep(step),
    })
    plainLines.push(`${String(stepIndex + 1).padStart(2, "0")}. ${describeStep(step)}`)
    if (step.action === "snapshot") {
      snapshots.push({ label: step.label, cleaned: `dry-run snapshot · ${step.label}` })
    }
    if (step.action === "snapshotBurst") {
      for (let burstIndex = 0; burstIndex < step.count; burstIndex += 1) {
        snapshots.push({
          label: `${step.label}-${String(burstIndex + 1).padStart(2, "0")}`,
          cleaned: `dry-run burst snapshot · ${step.label} · ${burstIndex + 1}/${step.count}`,
        })
      }
    }
  }
  if (snapshots.length === 0) {
    snapshots.push({ label: "terminal-adapter-dry-run", cleaned: "dry-run adapter ready" })
  }
  plainLines.push(`targetIds: ${JSON.stringify(targetIds)}`)
  const finishedAt = Date.now()
  const plainBuffer = `${plainLines.join("\n")}\n`
  return {
    snapshots,
    plainBuffer,
    visibleTextFinal: plainBuffer,
    scrollbackTextFinal: plainBuffer,
    stepEvents,
    frames: snapshots.map((snapshot, index) => ({
      timestamp: startedAt + index * 10,
      label: snapshot.label,
      text: snapshot.cleaned,
      screenshotPath: null,
    })),
    inputLogLines: [],
    metadata: {
      startedAt,
      finishedAt,
      durationMs: finishedAt - startedAt,
      lane: options.lane,
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      runtimeDir: path.resolve(options.runtimeDir),
      targetIds,
      capabilitySummary: capabilitySummaryForLane(options.lane),
      integrationMode: "dry-run",
      display: null,
      socketPath: null,
      x11WindowId: null,
    },
  }
}

const runWeztermTerminalAdapterHarness = async (
  options: TerminalAdapterHarnessOptions,
): Promise<TerminalAdapterHarnessResult> => {
  const inputLogLines: string[] = []
  const stepEvents: TerminalAdapterStepEvent[] = []
  const harnessStartedAt = Date.now()
  let targetIds = buildDeterministicTargetIds(options)
  let observerResult: Awaited<ReturnType<typeof runWeztermObserverHarness>>
  try {
    observerResult = await runWeztermObserverHarness({
      steps: options.steps,
      command: options.command,
      cwd: options.cwd,
      configPath: options.configPath,
      baseUrl: options.baseUrl,
      cols: options.cols,
      rows: options.rows,
      runtimeDir: options.runtimeDir,
      envOverrides: options.envOverrides,
      captureScreenshots: options.captureScreenshots,
      stateDumpPath: options.stateDumpPath,
      stateDumpMode: options.stateDumpMode,
      stateDumpRateMs: options.stateDumpRateMs,
      maxDurationMs: options.maxDurationMs,
      onInput: (entry) => inputLogLines.push(JSON.stringify(entry)),
      onStepStart: (entry) => {
        targetIds = normalizeWeztermTargetIds(options, entry)
        const now = Date.now()
        const label = entry.step.action === "snapshot"
          ? entry.step.label
          : `step-${String(entry.stepIndex + 1).padStart(2, "0")}-${entry.step.action}`
        stepEvents.push({
          stepId: `${options.lane}-step-${String(entry.stepIndex + 1).padStart(3, "0")}`,
          stepIndex: entry.stepIndex,
          action: entry.step.action,
          label,
          t_rel_ms: now - harnessStartedAt,
          t_wall_iso: new Date(now).toISOString(),
          targetIds,
          capturePolicy: capturePolicyForStep(entry.step),
        })
      },
    })
  } catch (error) {
    if (error instanceof EmulatorObserverHarnessError) {
      observerResult = error.result
      targetIds = normalizeWeztermTargetIds(options, {
        paneId: observerResult.metadata.paneId || targetIds.paneId,
        windowId: observerResult.metadata.windowId,
        tabId: observerResult.metadata.tabId,
        x11WindowId: observerResult.metadata.x11WindowId,
      })
      const result: TerminalAdapterHarnessResult = {
        snapshots: observerResult.snapshots,
        plainBuffer: observerResult.plainBuffer,
        visibleTextFinal: observerResult.plainBuffer,
        scrollbackTextFinal: observerResult.scrollbackBuffer,
        stepEvents,
        frames: observerResult.frames,
        inputLogLines,
        metadata: {
          startedAt: observerResult.metadata.startedAt,
          finishedAt: observerResult.metadata.finishedAt,
          durationMs: observerResult.metadata.durationMs,
          lane: "wezterm",
          command: options.command,
          cols: options.cols,
          rows: options.rows,
          runtimeDir: path.resolve(options.runtimeDir),
          targetIds,
          capabilitySummary: capabilitySummaryForLane("wezterm"),
          integrationMode: "wezterm",
          display: observerResult.metadata.display,
          socketPath: observerResult.metadata.socketPath,
          x11WindowId: observerResult.metadata.x11WindowId ?? null,
        },
        artifactDirs: {
          runtimeDir: path.join(options.runtimeDir, "observer_runtime"),
          textDir: path.join(options.runtimeDir, "observer_text"),
          pngDir: path.join(options.runtimeDir, "observer_png"),
        },
      }
      throw new TerminalAdapterHarnessError(error.message, result, error)
    }
    throw error
  }

  targetIds = normalizeWeztermTargetIds(options, {
    paneId: observerResult.metadata.paneId || targetIds.paneId,
    windowId: observerResult.metadata.windowId,
    tabId: observerResult.metadata.tabId,
    x11WindowId: observerResult.metadata.x11WindowId,
  })

  return {
    snapshots: observerResult.snapshots,
    plainBuffer: observerResult.plainBuffer,
    visibleTextFinal: observerResult.plainBuffer,
    scrollbackTextFinal: observerResult.scrollbackBuffer,
    stepEvents,
    frames: observerResult.frames,
    inputLogLines,
    metadata: {
      startedAt: observerResult.metadata.startedAt,
      finishedAt: observerResult.metadata.finishedAt,
      durationMs: observerResult.metadata.durationMs,
      lane: "wezterm",
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      runtimeDir: path.resolve(options.runtimeDir),
      targetIds,
      capabilitySummary: capabilitySummaryForLane("wezterm"),
      integrationMode: "wezterm",
      display: observerResult.metadata.display,
      socketPath: observerResult.metadata.socketPath,
      x11WindowId: observerResult.metadata.x11WindowId ?? null,
    },
    artifactDirs: {
      runtimeDir: path.join(options.runtimeDir, "observer_runtime"),
      textDir: path.join(options.runtimeDir, "observer_text"),
      pngDir: path.join(options.runtimeDir, "observer_png"),
    },
  }
}

const runGhosttyTerminalAdapterHarness = async (
  options: TerminalAdapterHarnessOptions,
): Promise<TerminalAdapterHarnessResult> => {
  let observerResult: Awaited<ReturnType<typeof runGhosttyObserverHarness>>
  try {
    observerResult = await runGhosttyObserverHarness({
      steps: options.steps,
      command: options.command,
      cwd: options.cwd,
      configPath: options.configPath,
      baseUrl: options.baseUrl,
      cols: options.cols,
      rows: options.rows,
      runtimeDir: options.runtimeDir,
      stateDumpPath: options.stateDumpPath,
      stateDumpMode: options.stateDumpMode,
      stateDumpRateMs: options.stateDumpRateMs,
      envOverrides: options.envOverrides,
      captureScreenshots: options.captureScreenshots,
    })
  } catch (error) {
    if (error instanceof GhosttyObserverHarnessError) {
      const targetIds = buildDeterministicTargetIds(options)
      const result: TerminalAdapterHarnessResult = {
        snapshots: error.result.snapshots,
        plainBuffer: error.result.plainBuffer,
        visibleTextFinal: error.result.plainBuffer,
        scrollbackTextFinal: error.result.scrollbackBuffer,
        stepEvents: [],
        frames: error.result.frames,
        inputLogLines: [],
        metadata: {
          startedAt: error.result.metadata.startedAt,
          finishedAt: error.result.metadata.finishedAt,
          durationMs: error.result.metadata.durationMs,
          lane: "ghostty",
          command: options.command,
          cols: options.cols,
          rows: options.rows,
          runtimeDir: path.resolve(options.runtimeDir),
          targetIds: {
            ...targetIds,
            windowId: error.result.metadata.x11WindowId ?? targetIds.windowId,
          },
          capabilitySummary: capabilitySummaryForLane("ghostty"),
          integrationMode: "ghostty",
          display: error.result.metadata.display,
          socketPath: null,
          x11WindowId: error.result.metadata.x11WindowId ?? null,
        },
        artifactDirs: {
          runtimeDir: path.join(options.runtimeDir, "observer_runtime"),
          textDir: path.join(options.runtimeDir, "observer_text"),
          pngDir: path.join(options.runtimeDir, "observer_png"),
        },
      }
      throw new TerminalAdapterHarnessError(error.message, result, error)
    }
    throw error
  }
  const targetIds = buildDeterministicTargetIds(options)
  return {
    snapshots: observerResult.snapshots,
    plainBuffer: observerResult.plainBuffer,
    visibleTextFinal: observerResult.plainBuffer,
    scrollbackTextFinal: observerResult.scrollbackBuffer,
    stepEvents: [],
    frames: observerResult.frames,
    inputLogLines: [],
    metadata: {
      startedAt: observerResult.metadata.startedAt,
      finishedAt: observerResult.metadata.finishedAt,
      durationMs: observerResult.metadata.durationMs,
      lane: "ghostty",
      command: options.command,
      cols: options.cols,
      rows: options.rows,
      runtimeDir: path.resolve(options.runtimeDir),
      targetIds: {
        ...targetIds,
        windowId: observerResult.metadata.x11WindowId ?? targetIds.windowId,
      },
      capabilitySummary: capabilitySummaryForLane("ghostty"),
      integrationMode: "ghostty",
      display: observerResult.metadata.display,
      socketPath: null,
      x11WindowId: observerResult.metadata.x11WindowId ?? null,
    },
    artifactDirs: {
      runtimeDir: path.join(options.runtimeDir, "observer_runtime"),
      textDir: path.join(options.runtimeDir, "observer_text"),
      pngDir: path.join(options.runtimeDir, "observer_png"),
    },
  }
}

export const runTerminalAdapterHarness = async (
  options: TerminalAdapterHarnessOptions,
): Promise<TerminalAdapterHarnessResult> => {
  if (options.lane === "dry-run") {
    return runDryRunTerminalAdapterHarness(options)
  }
  if (options.lane === "wezterm") {
    return runWeztermTerminalAdapterHarness(options)
  }
  if (options.lane === "ghostty") {
    return runGhosttyTerminalAdapterHarness(options)
  }
  throw new Error(`Terminal adapter lane ${options.lane} is not implemented yet`)
}
