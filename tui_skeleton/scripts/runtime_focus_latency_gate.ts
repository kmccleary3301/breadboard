import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { performance } from "node:perf_hooks"
import { evaluateFocusLatencyGate } from "../src/repl/components/replView/controller/focusLatency.js"

interface Args {
  strict: boolean
  out: string | null
  thresholdsPath: string | null
  openThresholdMs: number | null
  switchThresholdMs: number | null
}

const parseArgs = (): Args => {
  const argv = process.argv.slice(2)
  let strict = false
  let out: string | null = null
  let thresholdsPath: string | null = null
  let openThresholdMs: number | null = null
  let switchThresholdMs: number | null = null
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--strict") strict = true
    else if (arg === "--out") out = argv[++index] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++index] ?? null
    else if (arg === "--open-threshold-ms") openThresholdMs = Number(argv[++index] ?? "120")
    else if (arg === "--switch-threshold-ms") switchThresholdMs = Number(argv[++index] ?? "90")
  }
  return { strict, out, thresholdsPath, openThresholdMs, switchThresholdMs }
}

const loadThresholds = (
  customPath: string | null,
): {
  openP95Ms: number
  switchP95Ms: number
} => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    const openP95Ms = Number(parsed?.focusLatency?.openP95Ms)
    const switchP95Ms = Number(parsed?.focusLatency?.switchP95Ms)
    return {
      openP95Ms: Number.isFinite(openP95Ms) ? openP95Ms : 120,
      switchP95Ms: Number.isFinite(switchP95Ms) ? switchP95Ms : 90,
    }
  } catch {
    return {
      openP95Ms: 120,
      switchP95Ms: 90,
    }
  }
}

const buildLaneArtifact = (laneId: string, lineCount = 8_000): string => {
  const lines: string[] = []
  for (let index = 0; index < lineCount; index += 1) {
    const seq = String(index + 1).padStart(5, "0")
    lines.push(
      JSON.stringify({
        lane: laneId,
        seq,
        event: "task_update",
        status: index % 23 === 0 ? "blocked" : index % 17 === 0 ? "running" : "completed",
        detail: `deterministic fixture payload line ${seq} for ${laneId}`,
      }),
    )
  }
  return lines.join("\n")
}

const readTailSnippet = (artifactPath: string, tailLines: number, maxBytes: number): string[] => {
  const raw = readFileSync(artifactPath, "utf8")
  const bounded = raw.length > maxBytes ? raw.slice(raw.length - maxBytes) : raw
  const lines = bounded.replace(/\r\n?/g, "\n").split("\n")
  return lines.slice(Math.max(0, lines.length - tailLines))
}

const sampleLatencyMs = (artifactPath: string, tailLines: number, maxBytes: number): number => {
  const started = performance.now()
  void readTailSnippet(artifactPath, tailLines, maxBytes)
  return performance.now() - started
}

const main = (): void => {
  const args = parseArgs()
  const defaults = loadThresholds(args.thresholdsPath)
  const thresholds = {
    openP95Ms: args.openThresholdMs ?? defaults.openP95Ms,
    switchP95Ms: args.switchThresholdMs ?? defaults.switchP95Ms,
  }

  const tempDir = mkdtempSync(path.join(os.tmpdir(), "bb-focus-latency-"))
  const laneIds = ["lane-primary", "lane-research", "lane-coding", "lane-validation"]
  const tailLines = 24
  const maxBytes = 40_000
  const artifactPaths = laneIds.map((laneId) => {
    const artifactPath = path.join(tempDir, `${laneId}.jsonl`)
    writeFileSync(artifactPath, `${buildLaneArtifact(laneId)}\n`, "utf8")
    return artifactPath
  })

  try {
    const warmupSamples: number[] = []
    for (const artifactPath of artifactPaths) {
      warmupSamples.push(sampleLatencyMs(artifactPath, tailLines, maxBytes))
    }

    const openSamples: number[] = []
    for (let round = 0; round < 6; round += 1) {
      for (const artifactPath of artifactPaths) {
        openSamples.push(sampleLatencyMs(artifactPath, tailLines, maxBytes))
      }
    }

    const switchSamples: number[] = []
    for (let index = 0; index < 120; index += 1) {
      const artifactPath = artifactPaths[index % artifactPaths.length]
      switchSamples.push(sampleLatencyMs(artifactPath, tailLines, maxBytes))
    }

    const gate = evaluateFocusLatencyGate(openSamples, switchSamples, thresholds)
    const output = {
      ...gate,
      strict: args.strict,
      harness: {
        laneCount: artifactPaths.length,
        tailLines,
        maxBytes,
        warmupSamplesMs: warmupSamples,
      },
    }
    const rendered = JSON.stringify(output, null, 2)
    console.log(rendered)
    if (args.out) {
      writeFileSync(path.resolve(args.out), `${rendered}\n`, "utf8")
    }
    if (args.strict && !gate.ok) {
      process.exit(1)
    }
    process.exit(0)
  } finally {
    rmSync(tempDir, { recursive: true, force: true })
  }
}

main()
