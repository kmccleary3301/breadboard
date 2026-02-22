import path from "node:path"
import { fileURLToPath } from "node:url"
import { expect, test } from "@playwright/test"
import { installMockBridgeApi } from "../helpers/mockBridgeApi"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const replayFixturePath = path.resolve(__dirname, "../fixtures/replay_import.json")

test.beforeEach(async ({ page }) => {
  await installMockBridgeApi(page)
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "BreadBoard Webapp V1 (P0 Scaffold)" })).toBeVisible()
})

test("shell renders with deterministic diagnostics status", async ({ page }) => {
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Diagnostics" })).toBeVisible()

  await page.getByRole("button", { name: "Diagnostics" }).click()
  await expect(page.getByText(/diagnostics:\s*ok/i)).toBeVisible()
  await expect(page.getByText(/models=1/)).toBeVisible()
})

test("replay import hydrates transcript, tools, permissions, checkpoints, and task tree", async ({ page }) => {
  await page.getByTestId("replay-import-input").setInputFiles(replayFixturePath)

  const transcript = page.getByTestId("transcript-list")
  const tools = page.getByTestId("tool-rows")
  const checkpoints = page.getByTestId("checkpoint-list")
  const ledger = page.getByTestId("permission-ledger")
  const taskTree = page.getByTestId("task-tree")

  await expect(page.getByText("Projection hash:")).toContainText(/sha256:[a-f0-9]{64}/)
  await expect(transcript.getByText("Summarize release readiness.")).toBeVisible()
  await expect(transcript.getByText("Verification complete.")).toBeVisible()

  await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible()
  await expect(tools.getByText("write_file", { exact: true }).first()).toBeVisible()
  await expect(tools.getByText("Diff Viewer")).toBeVisible()

  await expect(page.getByRole("heading", { name: "Checkpoints" })).toBeVisible()
  await expect(checkpoints.getByText("Before release")).toBeVisible()

  await expect(page.getByRole("heading", { name: "Permission Ledger" })).toBeVisible()
  await expect(ledger.getByText("run_command")).toBeVisible()
  await expect(ledger.getByRole("button", { name: "Copy Rule" })).toBeVisible()

  await expect(page.getByRole("heading", { name: "Task Tree" })).toBeVisible()
  await expect(taskTree.getByText("Run verification")).toBeVisible()

  await page.getByPlaceholder("search transcript/tools/artifacts").fill("verification")
  await expect(page.getByText("No search matches.")).toBeHidden()
  await expect(page.getByRole("button", { name: /verification/i }).first()).toBeVisible()
})

test("connection mode and token policy persist across reload", async ({ page }) => {
  await page.getByLabel("Mode").selectOption("remote")
  await page.getByLabel("Token Storage").selectOption("session")
  await expect(page.getByText(/Remote mode trust boundary/i)).toBeVisible()

  await page.reload()

  await expect(page.getByLabel("Mode")).toHaveValue("remote")
  await expect(page.getByLabel("Token Storage")).toHaveValue("session")
  await expect(page.getByText(/Remote mode trust boundary/i)).toBeVisible()
})
