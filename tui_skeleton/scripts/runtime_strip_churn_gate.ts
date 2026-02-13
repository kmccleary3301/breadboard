import { readFileSync, writeFileSync } from "node:fs"
import path from "node:path"
import type { WorkGraphState, WorkItem } from "../src/repl/types.js"
import {
  buildSubagentStripSummary,
  evaluateSubagentStripChurn,
} from "../src/repl/components/replView/controller/subagentStrip.js"

interface Args {
  strict: boolean
  threshold: number | null
  out: string | null
  thresholdsPath: string | null
}

type ItemSeed = {
  readonly id: string
  readonly status: WorkItem["status"]
  readonly title: string
}

type ScenarioResult = {
  readonly name: string
  readonly samples: number
  readonly transitions: number
  readonly ratio: number
  readonly ok: boolean
}

const parseArgs = (): Args => {
  let strict = false
  let threshold: number | null = null
  let out: string | null = null
  let thresholdsPath: string | null = null
  const argv = process.argv.slice(2)
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === "--strict") strict = true
    else if (arg === "--threshold") threshold = Number(argv[++index] ?? "0.55")
    else if (arg === "--out") out = argv[++index] ?? null
    else if (arg === "--thresholds") thresholdsPath = argv[++index] ?? null
  }
  return { strict, threshold, out, thresholdsPath }
}

const loadThreshold = (customPath: string | null): number => {
  const fallbackPath = path.resolve("config/runtime_gate_thresholds.json")
  const target = customPath ? path.resolve(customPath) : fallbackPath
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    const value = Number(parsed?.stripChurn?.threshold)
    return Number.isFinite(value) ? value : 0.55
  } catch {
    return 0.55
  }
}

const buildGraph = (items: ReadonlyArray<ItemSeed>): WorkGraphState => {
  const itemsById: WorkGraphState["itemsById"] = {}
  const itemOrder: string[] = []
  for (const [offset, item] of items.entries()) {
    const id = item.id
    itemOrder.push(id)
    itemsById[id] = {
      workId: id,
      laneId: "lane-a",
      laneLabel: "lane-a",
      title: item.title,
      mode: "async",
      status: item.status,
      createdAt: 1_000 + offset,
      updatedAt: 2_000 + offset,
      parentWorkId: null,
      treePath: null,
      depth: null,
      artifactPaths: [],
      lastSafeExcerpt: null,
      steps: [],
      counters: { completed: 0, running: 0, failed: 0, total: 0 },
    }
  }
  return {
    itemsById,
    itemOrder,
    lanesById: {},
    laneOrder: [],
    processedEventKeys: [],
    lastSeq: 0,
  }
}

const runScenario = (
  name: string,
  sequence: ReadonlyArray<ReadonlyArray<ItemSeed>>,
  threshold: number,
): ScenarioResult => {
  const samples = sequence.map((items) => buildSubagentStripSummary(buildGraph(items)))
  const metrics = evaluateSubagentStripChurn(samples)
  return {
    name,
    samples: metrics.samples,
    transitions: metrics.transitions,
    ratio: metrics.ratio,
    ok: metrics.ratio <= threshold,
  }
}

const main = (): void => {
  const args = parseArgs()
  const threshold = args.threshold ?? loadThreshold(args.thresholdsPath)
  const scenarios: ScenarioResult[] = [
    runScenario(
      "steady-progress",
      [
        [{ id: "task-1", status: "running", title: "index docs" }],
        [{ id: "task-1", status: "running", title: "index docs" }],
        [{ id: "task-1", status: "running", title: "index docs" }],
        [{ id: "task-1", status: "completed", title: "index docs" }],
        [{ id: "task-1", status: "completed", title: "index docs" }],
      ],
      threshold,
    ),
    runScenario(
      "bounded-burst",
      [
        [{ id: "task-1", status: "running", title: "fetch lane" }],
        [{ id: "task-1", status: "running", title: "fetch lane" }],
        [
          { id: "task-1", status: "running", title: "fetch lane" },
          { id: "task-2", status: "blocked", title: "await output" },
        ],
        [
          { id: "task-1", status: "running", title: "fetch lane" },
          { id: "task-2", status: "blocked", title: "await output" },
        ],
        [
          { id: "task-1", status: "completed", title: "fetch lane" },
          { id: "task-2", status: "running", title: "await output" },
        ],
        [
          { id: "task-1", status: "completed", title: "fetch lane" },
          { id: "task-2", status: "running", title: "await output" },
        ],
      ],
      threshold,
    ),
  ]

  const failCount = scenarios.filter((scenario) => !scenario.ok).length
  const summary = {
    ok: failCount === 0,
    strict: args.strict,
    threshold,
    total: scenarios.length,
    failCount,
    scenarios,
  }
  const output = JSON.stringify(summary, null, 2)
  console.log(output)
  if (args.out) {
    writeFileSync(path.resolve(args.out), `${output}\n`, "utf8")
  }
  if (args.strict && failCount > 0) {
    process.exit(1)
  }
  process.exit(0)
}

main()
