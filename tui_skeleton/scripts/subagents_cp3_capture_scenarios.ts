import { appendFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { performance } from "node:perf_hooks"

const readTailSnippet = (artifactPath: string, tailLines: number, maxBytes: number): string[] => {
  const raw = readFileSync(artifactPath, "utf8")
  const bounded = raw.length > maxBytes ? raw.slice(raw.length - maxBytes) : raw
  const lines = bounded.replace(/\r\n?/g, "\n").split("\n")
  return lines.slice(Math.max(0, lines.length - tailLines))
}

const createLaneArtifacts = (tempDir: string, laneIds: string[]): Record<string, string> => {
  const byLane: Record<string, string> = {}
  for (const laneId of laneIds) {
    const artifactPath = path.join(tempDir, `${laneId}.jsonl`)
    const seed: string[] = []
    for (let index = 1; index <= 80; index += 1) {
      seed.push(JSON.stringify({ lane: laneId, seq: index, status: "running", message: `seed-${index}` }))
    }
    writeFileSync(artifactPath, `${seed.join("\n")}\n`, "utf8")
    byLane[laneId] = artifactPath
  }
  return byLane
}

const runActiveUpdatesScenario = (laneArtifactById: Record<string, string>) => {
  const laneId = Object.keys(laneArtifactById)[0] ?? "lane-a"
  const artifactPath = laneArtifactById[laneId]
  const tailLines = 24
  const maxBytes = 40_000
  const samplesMs: number[] = []
  const staleMismatchCount = { count: 0 }
  for (let step = 1; step <= 90; step += 1) {
    appendFileSync(
      artifactPath,
      `${JSON.stringify({ lane: laneId, seq: 80 + step, status: "running", message: `update-${step}` })}\n`,
      "utf8",
    )
    const started = performance.now()
    const lines = readTailSnippet(artifactPath, tailLines, maxBytes)
    const elapsed = performance.now() - started
    samplesMs.push(elapsed)
    const joined = lines.join("\n")
    if (!joined.includes(`"lane":"${laneId}"`)) staleMismatchCount.count += 1
  }
  const maxReadMs = Math.max(0, ...samplesMs)
  return {
    scenario: "cp3_focus_active_updates",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      noStaleLaneMixups: staleMismatchCount.count === 0,
      boundedReadLatency: maxReadMs < 50,
      sufficientSampleCount: samplesMs.length === 90,
    },
    metrics: {
      laneId,
      sampleCount: samplesMs.length,
      maxReadMs,
      p95ReadMs: samplesMs.slice().sort((a, b) => a - b)[Math.max(0, Math.ceil(samplesMs.length * 0.95) - 1)] ?? 0,
      staleMismatchCount: staleMismatchCount.count,
    },
  }
}

const runRapidSwitchScenario = (laneArtifactById: Record<string, string>) => {
  const laneIds = Object.keys(laneArtifactById)
  const tailLines = 24
  const maxBytes = 40_000
  const switchSamplesMs: number[] = []
  let staleMismatchCount = 0
  for (let i = 0; i < 200; i += 1) {
    const laneId = laneIds[i % laneIds.length] ?? laneIds[0]
    const artifactPath = laneArtifactById[laneId]
    const started = performance.now()
    const lines = readTailSnippet(artifactPath, tailLines, maxBytes)
    const elapsed = performance.now() - started
    switchSamplesMs.push(elapsed)
    const joined = lines.join("\n")
    if (!joined.includes(`"lane":"${laneId}"`)) staleMismatchCount += 1
  }
  const maxSwitchMs = Math.max(0, ...switchSamplesMs)
  return {
    scenario: "cp3_focus_rapid_lane_switch",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      noStaleLaneMixups: staleMismatchCount === 0,
      boundedSwitchReadLatency: maxSwitchMs < 50,
      sufficientSwitchCount: switchSamplesMs.length === 200,
    },
    metrics: {
      laneCount: laneIds.length,
      switchCount: switchSamplesMs.length,
      maxSwitchMs,
      p95SwitchMs:
        switchSamplesMs.slice().sort((a, b) => a - b)[Math.max(0, Math.ceil(switchSamplesMs.length * 0.95) - 1)] ?? 0,
      staleMismatchCount,
    },
  }
}

const main = (): void => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const tempDir = mkdtempSync(path.join(os.tmpdir(), "bb-cp3-focus-captures-"))
  const laneIds = ["lane-primary", "lane-research", "lane-coding", "lane-validation"]
  const artifacts = createLaneArtifacts(tempDir, laneIds)
  try {
    const captures = [runActiveUpdatesScenario(artifacts), runRapidSwitchScenario(artifacts)]
    for (const capture of captures) {
      const filePath = path.join(outputDir, `${capture.scenario}_capture_20260213.json`)
      writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
      console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
    }
  } finally {
    rmSync(tempDir, { recursive: true, force: true })
  }
}

main()
