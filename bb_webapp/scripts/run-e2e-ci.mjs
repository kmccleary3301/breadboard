import { spawnSync } from "node:child_process"

const run = (command, args) =>
  spawnSync(command, args, {
    cwd: process.cwd(),
    stdio: "inherit",
    env: process.env,
  })

const testResult = run("npx", ["playwright", "test", "--config=playwright.config.ts"])
run("node", ["./scripts/summarize-e2e-report.mjs"])

const code = typeof testResult.status === "number" ? testResult.status : 1
process.exit(code)
