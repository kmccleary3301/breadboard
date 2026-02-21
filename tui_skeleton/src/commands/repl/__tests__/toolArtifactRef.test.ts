import { describe, expect, it } from "vitest"
import { materializeToolArtifactRef, normalizeArtifactRef, shouldMaterializeToolArtifact } from "../toolArtifactRef.js"

describe("toolArtifactRef", () => {
  it("materializes deterministic refs for identical content", () => {
    const one = materializeToolArtifactRef({
      kind: "tool_output",
      mime: "text/plain",
      content: "alpha\nbeta\ngamma\n",
      previewMaxLines: 2,
    })
    const two = materializeToolArtifactRef({
      kind: "tool_output",
      mime: "text/plain",
      content: "alpha\nbeta\ngamma\n",
      previewMaxLines: 2,
    })
    expect(one.id).toBe(two.id)
    expect(one.sha256).toBe(two.sha256)
    expect(one.path).toBe(two.path)
    expect(one.preview?.lines).toEqual(["alpha", "beta"])
  })

  it("normalizes valid refs and rejects malformed refs", () => {
    const normalized = normalizeArtifactRef({
      schema_version: "artifact_ref_v1",
      id: "artifact-123",
      kind: "tool_result",
      mime: "text/plain",
      size_bytes: 42,
      sha256: "abc123",
      storage: "workspace_file",
      path: "docs_tmp/tui_tool_artifacts/tool_result/artifact-123.txt",
    })
    expect(normalized?.id).toBe("artifact-123")
    expect(normalizeArtifactRef({ schema_version: "artifact_ref_v1", id: "x" })).toBeNull()
  })

  it("flags oversized payloads for artifact materialization", () => {
    const small = "x".repeat(128)
    const large = "y".repeat(32_000)
    expect(shouldMaterializeToolArtifact(small, "tool_output")).toBe(false)
    expect(shouldMaterializeToolArtifact(large, "tool_output")).toBe(true)
    expect(shouldMaterializeToolArtifact(large, "tool_diff")).toBe(true)
  })
})
