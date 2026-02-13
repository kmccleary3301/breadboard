import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"
import { performance } from "node:perf_hooks"
import {
  __resetTaskFocusTailCacheForTests,
  getTaskFocusTailCacheStats,
  loadTaskFocusTail,
} from "../src/repl/components/replView/controller/taskFocusLoader.js"

const percentile = (values: number[], q: number): number => {
  if (values.length === 0) return 0
  const sorted = values.slice().sort((a, b) => a - b)
  const index = Math.max(0, Math.ceil(sorted.length * q) - 1)
  return sorted[index] ?? sorted[sorted.length - 1] ?? 0
}

const benchmark = async (cached: boolean, samples: number): Promise<{ durations: number[]; hitRatio: number }> => {
  __resetTaskFocusTailCacheForTests()
  let reads = 0
  const readFile = async (filePath: string) => {
    reads += 1
    await new Promise((resolve) => setTimeout(resolve, 1))
    return {
      path: filePath,
      content: `line-${reads}`,
      truncated: false,
    }
  }
  const durations: number[] = []
  for (let index = 0; index < samples; index += 1) {
    const started = performance.now()
    await loadTaskFocusTail(
      {
        id: "task-cache-bench",
        artifactPath: "/tmp/cache-bench.jsonl",
      },
      {
        rawMode: false,
        tailLines: 24,
        maxBytes: 40_000,
        disableCache: !cached,
        cacheTtlMs: 10_000,
        nowMs: 5_000 + index,
      },
      readFile as any,
    )
    durations.push(performance.now() - started)
  }
  const stats = getTaskFocusTailCacheStats()
  const requestCount = Math.max(1, stats.hits + stats.misses)
  const hitRatio = stats.hits / requestCount
  return { durations, hitRatio }
}

const main = async (): Promise<void> => {
  const outputDir = path.resolve("docs/subagents_scenarios")
  mkdirSync(outputDir, { recursive: true })
  const sampleCount = 120
  const uncached = await benchmark(false, sampleCount)
  const cached = await benchmark(true, sampleCount)
  const p95Uncached = percentile(uncached.durations, 0.95)
  const p95Cached = percentile(cached.durations, 0.95)
  const maxUncached = Math.max(0, ...uncached.durations)
  const maxCached = Math.max(0, ...cached.durations)
  const capture = {
    scenario: "cp3_focus_cache_benchmark",
    generatedAt: new Date().toISOString(),
    acceptanceChecks: {
      cachedP95Improves: p95Cached < p95Uncached,
      cachedMaxBounded: maxCached < maxUncached,
      cacheHitRatioHealthy: cached.hitRatio >= 0.75,
    },
    metrics: {
      sampleCount,
      uncached: {
        p95Ms: p95Uncached,
        maxMs: maxUncached,
      },
      cached: {
        p95Ms: p95Cached,
        maxMs: maxCached,
        hitRatio: cached.hitRatio,
      },
    },
  }
  const filePath = path.join(outputDir, "cp3_focus_cache_benchmark_capture_20260213.json")
  writeFileSync(filePath, `${JSON.stringify(capture, null, 2)}\n`, "utf8")
  console.log(`wrote ${path.relative(process.cwd(), filePath)}`)
}

void main()
