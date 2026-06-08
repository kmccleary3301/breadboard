import { describe, expect, it } from "vitest"
import { mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { evaluateMixedTranscriptProjection } from "../tools/assertions/mixedTranscriptProjectionCheck.ts"

const writeCase = async (snapshots: string, cells: unknown[]) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), "bb-mixed-transcript-"))
  await writeFile(path.join(dir, "pty_snapshots.txt"), snapshots, "utf8")
  await writeFile(path.join(dir, "repl_state.ndjson"), JSON.stringify({ state: { transcriptCells: cells } }) + "\n", "utf8")
  return dir
}

describe("evaluateMixedTranscriptProjection", () => {
  it("accepts mixed transcript projection evidence", async () => {
    const dir = await writeCase(
      [
        "# tool-diff-transcript-before-permission",
        "H5_TOOL_SUCCESS H5_TOOL_ERROR H5_DETAIL_TRUNCATED Patch(src/h5_example.ts)",
        "# permission-summary-modal",
        "Permission required write_files src/h5_example.ts",
        "# permission-diff-modal",
        "src/h5_example.ts",
        "# h5-transcript-viewer",
        "breadboard transcript viewer [permission]",
        "Press / to search",
        "",
      ].join("\n"),
      [
        { role: "user-request", textPreview: "H5_USER_REQUEST" },
        { role: "assistant-message", textPreview: "H5_ASSISTANT_FINAL" },
        { role: "tool-result", textPreview: "H5_TOOL_SUCCESS" },
        { role: "tool-error", textPreview: "H5_TOOL_ERROR" },
        { role: "diff", textPreview: "src/h5_example.ts" },
        { role: "approval", textPreview: "H5_PERMISSION_ALLOWED" },
      ],
    )
    await expect(evaluateMixedTranscriptProjection(dir)).resolves.toEqual([])
    await rm(dir, { recursive: true, force: true })
  })
})
