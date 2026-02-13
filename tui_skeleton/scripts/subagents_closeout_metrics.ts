import { spawnSync } from "node:child_process"

type GateResult = {
  readonly name: string
  readonly command: string
  readonly ok: boolean
  readonly durationMs: number
  readonly exitCode: number
}

const runGate = (name: string, command: string): GateResult => {
  const start = Date.now()
  const result = spawnSync("bash", ["-lc", command], {
    cwd: process.cwd(),
    stdio: "pipe",
    encoding: "utf8",
  })
  const durationMs = Date.now() - start
  const exitCode = typeof result.status === "number" ? result.status : 1
  return {
    name,
    command,
    ok: exitCode === 0,
    durationMs,
    exitCode,
  }
}

const main = (): void => {
  const gates = [
    runGate("typecheck", "npm run typecheck"),
    runGate(
      "taskboard-focus-tests",
      "npm run test -- src/repl/components/replView/controller/__tests__/taskboardStatus.test.ts src/repl/components/replView/controller/keyHandlers/overlay/__tests__/handleListOverlayKeys.test.ts src/commands/repl/__tests__/controllerWorkGraphRuntime.test.ts src/commands/repl/__tests__/controllerSubagentRouting.test.ts",
    ),
    runGate("warn-noise-gate", "npm run runtime:gate:noise:warn"),
    runGate("warn-strip-churn-gate", "npm run runtime:gate:strip-churn:warn"),
    runGate("ascii-no-color", "npm run runtime:validate:ascii-no-color"),
  ]

  const failed = gates.filter((gate) => !gate.ok)
  const summary = {
    ok: failed.length === 0,
    total: gates.length,
    failed: failed.length,
    generatedAt: new Date().toISOString(),
    gates,
  }
  console.log(JSON.stringify(summary, null, 2))
  process.exit(summary.ok ? 0 : 1)
}

main()
