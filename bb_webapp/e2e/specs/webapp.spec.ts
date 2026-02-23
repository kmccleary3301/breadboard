import path from "node:path"
import { fileURLToPath } from "node:url"
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test"
import { installMockBridgeApi } from "../helpers/mockBridgeApi"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const replayFixturePath = path.resolve(__dirname, "../fixtures/replay_import.json")

const createAndAttachSession = async (task: string, page: Page): Promise<void> => {
  await page.getByLabel("Task").fill(task)
  await page.getByRole("button", { name: "Create + Attach" }).click()
}

const captureCheckpoint = async (page: Page, testInfo: TestInfo, name: string): Promise<void> => {
  const sanitized = name.toLowerCase().replace(/[^a-z0-9]+/g, "-")
  const screenshotPath = testInfo.outputPath(`${sanitized}.png`)
  await page.screenshot({ path: screenshotPath, fullPage: true })
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  })
}

const capturePanel = async (target: Locator, testInfo: TestInfo, name: string): Promise<void> => {
  const sanitized = name.toLowerCase().replace(/[^a-z0-9]+/g, "-")
  const screenshotPath = testInfo.outputPath(`${sanitized}.png`)
  await target.screenshot({ path: screenshotPath })
  await testInfo.attach(name, {
    path: screenshotPath,
    contentType: "image/png",
  })
}

test.beforeEach(async ({ page }) => {
  await installMockBridgeApi(page)
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "BreadBoard Webapp V1 (P0 Scaffold)" })).toBeVisible()
})

test("shell renders with deterministic diagnostics status", async ({ page }, testInfo) => {
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Transcript" })).toBeVisible()
  await expect(page.getByRole("button", { name: "Diagnostics" })).toBeVisible()

  await page.getByRole("button", { name: "Diagnostics" }).click()
  await expect(page.getByText(/diagnostics:\s*ok/i)).toBeVisible()
  await expect(page.getByText(/models=1/)).toBeVisible()
  await captureCheckpoint(page, testInfo, "shell-diagnostics-ok")
})

test("replay import hydrates transcript, tools, permissions, checkpoints, and task tree", async ({ page }, testInfo) => {
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
  await captureCheckpoint(page, testInfo, "replay-import-hydrated")
  await capturePanel(tools, testInfo, "replay-import-tools-panel")
  await capturePanel(taskTree, testInfo, "replay-import-task-tree-panel")
})

test("connection mode and token policy persist across reload", async ({ page }, testInfo) => {
  await page.getByLabel("Mode").selectOption("remote")
  await page.getByLabel("Token Storage").selectOption("session")
  await page.getByLabel("Engine Base URL").fill("http://127.0.0.1:5000")
  await expect(page.getByText(/Remote mode trust boundary/i)).toBeVisible()

  await page.reload()

  await expect(page.getByLabel("Mode")).toHaveValue("remote")
  await expect(page.getByLabel("Token Storage")).toHaveValue("session")
  await expect(page.getByLabel("Engine Base URL")).toHaveValue("http://127.0.0.1:5000")
  await expect(page.getByText(/Remote mode trust boundary/i)).toBeVisible()
  await page.getByRole("button", { name: "Check", exact: true }).click()
  await expect(page.getByText(/connected:\s*protocol=/i)).toBeVisible()
  await expect(page.getByText(/Failed to fetch/i)).toBeHidden()
  await captureCheckpoint(page, testInfo, "connection-mode-persistence")
})

test("live workflow: create attach send permissions checkpoints files and artifacts", async ({ page }, testInfo) => {
  await createAndAttachSession("run standard workflow", page)

  await expect(page.getByTestId("connection-state-pill")).toContainText(/streaming|connecting/)
  await expect(page.getByRole("button", { name: "mock-session-1" })).toBeVisible()
  await expect(page.getByTestId("transcript-list").getByText("bootstrap stream event")).toBeVisible()
  await captureCheckpoint(page, testInfo, "live-workflow-attached")
  await capturePanel(page.getByTestId("transcript-list"), testInfo, "live-workflow-transcript-panel")

  const messageInput = page.getByPlaceholder("Send message...")
  const permissionList = page.getByTestId("permission-list")
  await messageInput.fill("please continue")
  await page.getByRole("button", { name: "Send" }).click()
  await expect(messageInput).toHaveValue("")

  await expect(permissionList.getByText("Run CI test command")).toBeVisible()
  await permissionList.getByRole("button", { name: "Allow Once" }).first().click()
  await expect(permissionList.getByText("No pending permission requests.")).toBeVisible()
  await expect(page.getByText(/permission\.decision/)).toBeVisible()

  page.once("dialog", (dialog) => void dialog.accept())
  await page.getByRole("button", { name: "Restore" }).click()
  await expect(page.getByText(/checkpoint\.restore/)).toBeVisible()

  await page.getByRole("button", { name: /README\.md/ }).click()
  await expect(page.getByText("README snippet content")).toBeVisible()

  await page.getByPlaceholder("artifact id/path").fill("artifact-1.log")
  await page.getByRole("button", { name: "Download" }).click()
  await expect(page.getByText(/artifact\.download/)).toBeVisible()
  await captureCheckpoint(page, testInfo, "live-workflow-complete")
  await capturePanel(permissionList, testInfo, "live-workflow-permissions-panel")
  await capturePanel(page.getByTestId("tool-rows"), testInfo, "live-workflow-tools-panel")
  await capturePanel(page.getByTestId("task-tree"), testInfo, "live-workflow-task-tree-panel")
})

test("gap workflow: sequence gap surfaces recover flow and returns to active stream", async ({ page }, testInfo) => {
  await createAndAttachSession("run gap workflow", page)
  await expect(page.getByTestId("connection-state-pill")).toContainText("gap")
  await captureCheckpoint(page, testInfo, "gap-workflow-gap-state")

  const recover = page.getByRole("button", { name: "Recover Stream" })
  await expect(recover).toBeEnabled()
  await recover.click()

  await expect(page.getByTestId("connection-state-pill")).toContainText(/streaming|connecting/)
  await expect(page.getByText("gap bootstrap event")).toBeVisible()
  await captureCheckpoint(page, testInfo, "gap-workflow-recovered")
})
