import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { resolveTuiConfig } from "../src/tui_config/load.js"
import { buildLaneDiagnosticsHeatmap } from "../src/repl/components/replView/controller/diagnosticsHeatmap.js"

const withIsolatedConfig = async <T>(fn: (workspace: string) => Promise<T>): Promise<T> => {
  const root = mkdtempSync(path.join(os.tmpdir(), "bb-subagents-expansion-capture-"))
  const workspace = path.join(root, "workspace")
  const home = path.join(root, "home")
  mkdirSync(path.join(workspace, ".breadboard"), { recursive: true })
  mkdirSync(path.join(home, ".config", "breadboard"), { recursive: true })
  const previousHome = process.env.HOME
  process.env.HOME = home
  try {
    return await fn(workspace)
  } finally {
    if (previousHome == null) delete process.env.HOME
    else process.env.HOME = previousHome
    rmSync(root, { recursive: true, force: true })
  }
}

const runEx1SwapCapture = async () => {
  const summary = await withIsolatedConfig(async (workspace) => {
    const swapPreset = await resolveTuiConfig({
      workspace,
      cliPreset: "claude_like_subagents_swap",
      cliStrict: true,
      colorAllowed: true,
    })
    const lanePreset = await resolveTuiConfig({
      workspace,
      cliPreset: "claude_like_subagents",
      cliStrict: true,
      colorAllowed: true,
    })
    return {
      swapPreset,
      lanePreset,
    }
  })
  return {
    scenario: "ex1_focus_swap",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      swapPresetResolves: summary.swapPreset.subagents.focusMode === "swap",
      swapPresetKeepsTaskboardEnabled: summary.swapPreset.subagents.taskboardEnabled === true,
      baselineLanePresetRemainsLane: summary.lanePreset.subagents.focusMode === "lane",
      compatibilityDefaultsRemainEnabled: summary.swapPreset.subagents.enabled === true && summary.lanePreset.subagents.enabled === true,
    },
    summary: {
      swapPreset: {
        preset: summary.swapPreset.preset,
        focusMode: summary.swapPreset.subagents.focusMode,
        focusEnabled: summary.swapPreset.subagents.focusEnabled,
        taskboardEnabled: summary.swapPreset.subagents.taskboardEnabled,
      },
      baselinePreset: {
        preset: summary.lanePreset.preset,
        focusMode: summary.lanePreset.subagents.focusMode,
      },
    },
  }
}

const runEx3HeatmapCapture = () => {
  const heatmap = buildLaneDiagnosticsHeatmap(
    [
      { laneId: "lane-research", status: "running", updatedAt: 9_990 },
      { laneId: "lane-research", status: "failed", updatedAt: 9_995 },
      { laneId: "lane-coding", status: "running", updatedAt: 9_960 },
      { laneId: "lane-coding", status: "running", updatedAt: 9_970 },
      { laneId: "lane-tests", status: "blocked", updatedAt: 9_980 },
      { laneId: "lane-archive", status: "pending", updatedAt: 2_000 },
    ],
    {
      nowMs: 10_000,
      maxRows: 6,
      laneLabelById: {
        "lane-research": "Research",
        "lane-coding": "Coding",
        "lane-tests": "Tests",
      },
    },
  )
  const top = heatmap[0]
  const uniqueIntensities = new Set(heatmap.map((entry) => entry.intensity))
  return {
    scenario: "ex3_diagnostics_heatmap",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      producesRankedRows: heatmap.length >= 3,
      highestLaneIsResearch: top?.laneId === "lane-research",
      scoreOrderingMonotonic: heatmap.every((entry, index) => index === 0 || (heatmap[index - 1]?.score ?? 0) >= entry.score),
      bucketizationPresent: uniqueIntensities.size >= 2,
    },
    summary: {
      rowCount: heatmap.length,
      topLane: top?.laneId ?? null,
      intensities: Array.from(uniqueIntensities),
      rows: heatmap,
    },
  }
}

const runEx4PresetCapture = async () => {
  const summary = await withIsolatedConfig(async (workspace) => {
    const claudeSwap = await resolveTuiConfig({
      workspace,
      cliPreset: "claude_like_subagents_swap",
      cliStrict: true,
      colorAllowed: true,
    })
    const codexDense = await resolveTuiConfig({
      workspace,
      cliPreset: "codex_like_subagents_dense",
      cliStrict: true,
      colorAllowed: true,
    })
    const opencodeBaseline = await resolveTuiConfig({
      workspace,
      cliPreset: "opencode_like_subagents",
      cliStrict: true,
      colorAllowed: true,
    })
    return { claudeSwap, codexDense, opencodeBaseline }
  })
  return {
    scenario: "ex4_preset_variants",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      claudeSwapFocusModeSet: summary.claudeSwap.subagents.focusMode === "swap",
      codexDenseWorkloadExpanded:
        summary.codexDense.subagents.maxWorkItems > summary.opencodeBaseline.subagents.maxWorkItems &&
        summary.codexDense.subagents.maxStepsPerTask > summary.opencodeBaseline.subagents.maxStepsPerTask,
      legacyPresetCompatibility: summary.opencodeBaseline.subagents.focusMode === "lane",
    },
    summary: {
      claudeSwap: {
        preset: summary.claudeSwap.preset,
        focusMode: summary.claudeSwap.subagents.focusMode,
        maxWorkItems: summary.claudeSwap.subagents.maxWorkItems,
      },
      codexDense: {
        preset: summary.codexDense.preset,
        focusMode: summary.codexDense.subagents.focusMode,
        maxWorkItems: summary.codexDense.subagents.maxWorkItems,
        maxStepsPerTask: summary.codexDense.subagents.maxStepsPerTask,
      },
      opencodeBaseline: {
        preset: summary.opencodeBaseline.preset,
        focusMode: summary.opencodeBaseline.subagents.focusMode,
        maxWorkItems: summary.opencodeBaseline.subagents.maxWorkItems,
        maxStepsPerTask: summary.opencodeBaseline.subagents.maxStepsPerTask,
      },
    },
  }
}

const main = async (): Promise<void> => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const captures = [await runEx1SwapCapture(), runEx3HeatmapCapture(), await runEx4PresetCapture()]
  for (const capture of captures) {
    const filePath = path.join(outputDir, `${capture.scenario}_capture_20260213.json`)
    writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
    console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
  }
}

void main()
