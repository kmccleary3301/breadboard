import { describe, expect, it } from "vitest"
import { buildTranscriptViewerModel } from "../transcriptViewerModel.js"
import type { TranscriptItem } from "../../../../transcriptModel.js"

const cast = <T,>(value: T): T => value

describe("buildTranscriptViewerModel", () => {
  it("captures latest message, tool, warning, and error anchors", () => {
    const model = buildTranscriptViewerModel([
      cast<TranscriptItem>({
        id: "msg:user",
        kind: "message",
        createdAt: 1,
        source: "conversation",
        speaker: "user",
        text: "alpha request",
        phase: "final",
      }),
      cast<TranscriptItem>({
        id: "msg:assistant",
        kind: "message",
        createdAt: 2,
        source: "conversation",
        speaker: "assistant",
        text: "alpha response",
        phase: "final",
      }),
      cast<TranscriptItem>({
        id: "tool:1",
        kind: "tool",
        createdAt: 3,
        source: "tool",
        toolKind: "result",
        text: "saved artifact.txt",
      }),
      cast<TranscriptItem>({
        id: "sys:warning",
        kind: "system",
        createdAt: 4,
        source: "system",
        systemKind: "warning",
        text: "warning: degraded mode",
      }),
      cast<TranscriptItem>({
        id: "sys:error",
        kind: "system",
        createdAt: 5,
        source: "system",
        systemKind: "error",
        text: "error: missing path",
      }),
    ])

    expect(model.lines.some((line) => line.includes("alpha request"))).toBe(true)
    expect(model.lines.some((line) => line.includes("alpha response"))).toBe(true)
    expect(model.toolLines).toEqual([4])
    expect(model.anchors.user).toBe(0)
    expect(model.anchors.assistant).toBe(2)
    expect(model.anchors.tool).toBe(4)
    expect(model.anchors.warning).toBe(6)
    expect(model.anchors.error).toBe(8)
  })

  it("captures transcript tool targets with detail and artifact preview metadata", () => {
    const model = buildTranscriptViewerModel([
      cast<TranscriptItem>({
        id: "tool:artifact",
        kind: "tool",
        createdAt: 1,
        source: "tool",
        toolKind: "result",
        status: "success",
        text: "Write(report.txt)",
        display: {
          title: "Write(report.txt)",
          summary: "Saved report.txt",
          detail: ["Saved report.txt", "Artifact persisted for inspection."],
          detail_artifact: {
            schema_version: "artifact_ref_v1",
            id: "artifact-report",
            kind: "tool_output",
            mime: "text/plain",
            size_bytes: 128,
            sha256: "deadbeef",
            storage: "workspace_file",
            path: "artifacts/report.txt",
            preview: {
              lines: ["Report line 1", "Report line 2"],
              note: "Preview truncated to 2 lines.",
            },
          },
        },
      }),
    ])

    expect(model.toolTargets).toHaveLength(1)
    expect(model.toolTargets[0]).toMatchObject({
      line: 0,
      itemId: "tool:artifact",
      title: "Write(report.txt)",
      summaryLines: ["Saved report.txt"],
      detailLines: ["Saved report.txt", "Artifact persisted for inspection."],
      artifactPath: "artifacts/report.txt",
      artifactPreviewLines: ["Report line 1", "Report line 2"],
      artifactPreviewNote: "Preview truncated to 2 lines.",
    })
  })
})
