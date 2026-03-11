import test from "node:test"
import assert from "node:assert/strict"

import {
  buildWorkspaceCapabilitySet,
  createWorkspace,
  shapeTerminalOutput,
  shapeToolOutput,
  stripAnsi,
  supportsExecutionProfile,
} from "../src/index.js"

test("stripAnsi removes terminal escape sequences", () => {
  assert.equal(stripAnsi("\u001b[31mhello\u001b[0m"), "hello")
})

test("shapeToolOutput truncates while preserving head and tail", () => {
  const longText = `${"a".repeat(900)}${"b".repeat(600)}`
  const shaped = shapeToolOutput(longText)
  assert.equal(shaped.truncated, true)
  assert.match(shaped.userVisibleText, /\.\.\./)
  assert.ok(shaped.modelVisibleText.length <= 603)
})

test("shapeTerminalOutput records chunk count while preserving shaping semantics", () => {
  const shaped = shapeTerminalOutput("terminal output", { chunkCount: 3 })
  assert.equal(shaped.chunkCount, 3)
  assert.equal(shaped.userVisibleText, "terminal output")
})

test("workspace capability defaults choose trusted_local when available", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-1",
    rootDir: "/tmp/project",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  assert.equal(workspace.defaultExecutionProfileId, "trusted_local")
  assert.equal(workspace.defaultExecutionProfile.backendHint, "inline")
})

test("supportsExecutionProfile respects sandbox and remote capability flags", () => {
  const capabilities = buildWorkspaceCapabilitySet({
    canRunTrustedLocal: false,
    canRunSandboxedLocal: true,
    canRunRemoteIsolated: true,
  })
  assert.equal(supportsExecutionProfile(capabilities, "trusted_local"), false)
  assert.equal(supportsExecutionProfile(capabilities, "sandboxed_local"), true)
  assert.equal(supportsExecutionProfile(capabilities, "remote_isolated"), true)
})

test("workspace returns rich execution profile metadata", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-2",
    capabilitySet: buildWorkspaceCapabilitySet({ canRunRemoteIsolated: true }),
  })
  const profile = workspace.getExecutionProfile("remote_isolated")
  assert.equal(profile.placementHint, "remote_worker")
  assert.equal(profile.securityTierHint, "multi_tenant")
  assert.equal(profile.backendHint, "remote")
  assert.ok(profile.recommendedFor.includes("remote workers"))
})

test("workspace exposes terminal output shaping", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-3",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const shaped = workspace.shapeTerminalOutput("hello", { chunkCount: 2 })
  assert.equal(shaped.chunkCount, 2)
  assert.equal(shaped.userVisibleText, "hello")
  assert.equal(shaped.modelVisibleText, "hello")
})

test("workspace exposes terminal output delta shaping", () => {
  const workspace = createWorkspace({
    workspaceId: "ws-4",
    capabilitySet: buildWorkspaceCapabilitySet(),
  })
  const shaped = workspace.shapeTerminalOutputDeltas([
    { chunk_b64: Buffer.from("hello\n", "utf8").toString("base64") },
    { chunk_b64: Buffer.from("world\n", "utf8").toString("base64") },
  ])
  assert.equal(shaped.chunkCount, 2)
  assert.equal(shaped.userVisibleText, "hello\nworld\n")
  assert.equal(shaped.modelVisibleText, "hello\nworld\n")
})
