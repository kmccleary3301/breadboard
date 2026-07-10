import { mkdir, mkdtemp, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

import { evaluateE4RealHarnessParity } from "../tools/assertions/e4RealHarnessParityCheck.ts"

const writeJsonl = async (filePath: string, events: unknown[]) => {
  await writeFile(filePath, `${events.map((event) => JSON.stringify(event)).join("\n")}\n`, "utf8")
}

const makeCase = async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bb-e4-parity-"))
  const caseDir = path.join(root, "case")
  await mkdir(caseDir, { recursive: true })
  const repoRoot = path.join(root, "repo")
  const workspace = path.join(root, "bb_tmp_harness_parity_dummy", "session_e4_real")
  await mkdir(path.join(workspace, "src"), { recursive: true })
  await writeFile(
    path.join(caseDir, "config.json"),
    JSON.stringify({
      command: "bb repl",
      configPath: path.join(repoRoot, "agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      commandProvenance: { cwd: workspace },
    }),
    "utf8",
  )
  await writeFile(path.join(caseDir, "case_info.json"), JSON.stringify({ repro: { cwd: workspace } }), "utf8")
  await writeFile(
    path.join(caseDir, "repl_state.ndjson"),
    `${JSON.stringify({
      state: {
        stats: { model: "openai/gpt-5.1-codex-mini" },
        pendingResponse: false,
        lastConversation: { speaker: "assistant", phase: "final" },
        counts: { conversation: 6 },
        lastToolEvent: { status: "success" },
      },
    })}\n`,
    "utf8",
  )
  return { caseDir, workspace }
}

describe("e4RealHarnessParityCheck", () => {
  it("flags semantic loop failures that old marker checks missed", async () => {
    const { caseDir } = await makeCase()
    const events = [
      { type: "tool_result", payload: { success: true, metadata: { command: "pwd" } } },
      {
        type: "ctree_node",
        payload: {
          node: {
            payload: {
              payload: {
                user_prompt: "Do this and answer HARNESS_PARITY_INSPECT_OK.Now do that and answer HARNESS_PARITY_EDIT_OK.",
              },
            },
          },
        },
      },
      { type: "assistant_message", payload: { text: "I’ll inspect the workspace next and then report back." } },
      { type: "assistant_message", payload: { text: "I’m checking the files now." } },
      { type: "assistant_message", payload: { text: "Next I will verify the source." } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "find .. -name AGENTS.md -print" }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "git status --short" }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "rg -n widget -S ." }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "rg -n ALPHA_WIDGET -S ." }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "pwd" }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "cat notes.md" }) } } } },
      { type: "tool_call", payload: { call: { function: { arguments: JSON.stringify({ command: "cat src/widget.ts" }) } } } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "tool_result", payload: { success: true } },
      { type: "run_finished" },
      { type: "completion" },
    ]
    await writeJsonl(path.join(caseDir, "events.ndjson"), events)
    await writeFile(path.join(caseDir, "pty_snapshots.txt"), "HARNESS_PARITY_INSPECT_OK\n", "utf8")

    const anomalies = await evaluateE4RealHarnessParity(caseDir)
    const ids = anomalies.map((anomaly) => anomaly.id)
    expect(ids).toContain("excessive-tool-churn")
    expect(ids).toContain("progress-loop-after-tool")
    expect(ids).toContain("parent-directory-search")
    expect(ids).toContain("concatenated-user-turn-contract")
    expect(ids).toContain("turn-contract-not-preserved")
  })
})
