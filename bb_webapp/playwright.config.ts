import { defineConfig } from "@playwright/test"

const DEBUG_MODE = process.env.PLAYWRIGHT_E2E_DEBUG === "1"
const SKIP_WEBSERVER = process.env.PWTEST_SKIP_WEBSERVER === "1"

export default defineConfig({
  testDir: "./e2e/specs",
  outputDir: "../artifacts/webapp_e2e/test-results",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  retries: process.env.CI ? 1 : 0,
  workers: DEBUG_MODE ? 1 : undefined,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../artifacts/webapp_e2e/html-report" }],
    ["json", { outputFile: "../artifacts/webapp_e2e/report.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: DEBUG_MODE ? "on" : "retain-on-failure",
    screenshot: "only-on-failure",
    video: DEBUG_MODE ? "on" : "retain-on-failure",
  },
  webServer: SKIP_WEBSERVER
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1 --port 4173",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: "pipe",
        stderr: "pipe",
      },
})
