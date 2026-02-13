import { readFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../src/commands/repl/controller.js"
import { evaluateTranscriptNoiseGate } from "../src/commands/repl/transcriptNoiseGate.js"

type RawEvent = Record<string, unknown>

const FIXTURES = [
  "src/commands/repl/__tests__/fixtures/assistant_tool_interleave.jsonl",
  "src/commands/repl/__tests__/fixtures/inline_thinking_permission_tool_answer.jsonl",
  "src/commands/repl/__tests__/fixtures/runtime_plan_path.jsonl",
]

const loadNoiseThreshold = (): number => {
  const target = path.resolve("config/runtime_gate_thresholds.json")
  try {
    const parsed = JSON.parse(readFileSync(target, "utf8")) as any
    const value = Number(parsed?.noise?.threshold)
    return Number.isFinite(value) ? value : 0.8
  } catch {
    return 0.8
  }
}

const parseEvent = (raw: string): RawEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return parsed.event as RawEvent
    if (typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return inner as RawEvent
      } catch {
        return null
      }
    }
    if (parsed.type) return parsed as RawEvent
  }
  return null
}

const stateFromFixture = (fixturePath: string): any => {
  const controller = new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
  }
  const lines = readFileSync(path.resolve(fixturePath), "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    controller.applyEvent(event)
  }
  return controller.getState()
}

const main = (): void => {
  const threshold = loadNoiseThreshold()
  const results = FIXTURES.map((fixture) => {
    const gate = evaluateTranscriptNoiseGate(stateFromFixture(fixture), threshold)
    return { fixture, ...gate }
  })
  const failCount = results.filter((entry) => !entry.ok).length
  const summary = {
    ok: failCount === 0,
    strict: false,
    threshold,
    total: results.length,
    failCount,
    results,
  }
  console.log(JSON.stringify(summary, null, 2))
  if (failCount > 0) {
    console.warn(`[warn-only] transcript noise gate exceeded in ${failCount}/${results.length} fixture(s).`)
  }
  process.exit(0)
}

main()
