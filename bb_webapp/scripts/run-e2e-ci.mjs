import { spawnSync } from "node:child_process"

const run = (command, args) =>
  spawnSync(command, args, {
    cwd: process.cwd(),
    stdio: "inherit",
    env: process.env,
  })

const testResult = run("npx", ["playwright", "test", "--config=playwright.config.ts"])
const summaryResult = run("node", ["./scripts/summarize-e2e-report.mjs"])
const validationResult = run("node", ["./scripts/validate-e2e-artifacts.mjs"])

const resultCodes = [testResult.status, summaryResult.status, validationResult.status].map((value) =>
  typeof value === "number" ? value : 1,
)
const code = resultCodes.find((value) => value !== 0) ?? 0
process.exit(code)
