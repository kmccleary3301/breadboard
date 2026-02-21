import { describe, expect, test } from "bun:test"

import {
  classifyToolName,
  deriveSlabDisplayHints,
  formatNormalizedEventForStdout,
  normalizeBridgeEvent,
  type NormalizedEvent,
} from "./event_adapter.ts"
import type { BridgeEvent } from "./bridge.ts"

const mkEvent = (
  type: string,
  payload: Record<string, unknown> = {},
  extras: Partial<BridgeEvent> = {},
): BridgeEvent => ({
  id: "42",
  seq: 42,
  session_id: "sess-1",
  turn: 1,
  timestamp: 1000,
  type,
  payload,
  ...extras,
})

const expectSummaryAndHints = (normalized: NormalizedEvent) => {
  expect(normalized.summary.title.length).toBeGreaterThan(0)
  expect(normalized.summary.short.length).toBeGreaterThan(0)
  expect(normalized.hints.badge.length).toBeGreaterThan(0)
}

describe("normalizeBridgeEvent", () => {
  test("normalizes assistant_delta", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("assistant_delta", {
        message_id: "m-1",
        text: "hello from delta",
      }),
    )

    expect(normalized.type).toBe("message.delta")
    expect(normalized.actor?.kind).toBe("assistant")
    expect(normalized.messageId).toBe("m-1")
    expect(normalized.textDelta).toBe("hello from delta")
    expect(normalized.summary.title).toBe("Assistant delta")
    expect(normalized.summary.short).toBe("hello from delta")
    expect(normalized.hints).toEqual({
      lane: "assistant",
      badge: "assistant",
      tone: "info",
      priority: "low",
      stream: true,
    })
    expectSummaryAndHints(normalized)
  })

  test("normalizes assistant_message", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("assistant_message", {
        id: "m-2",
        text: "final assistant response",
      }),
    )

    expect(normalized.type).toBe("message.final")
    expect(normalized.actor?.kind).toBe("assistant")
    expect(normalized.messageId).toBe("m-2")
    expect(normalized.summary.title).toBe("Assistant message")
    expect(normalized.summary.short).toBe("final assistant response")
    expect(normalized.hints.stream).toBe(false)
    expect(normalized.hints.lane).toBe("assistant")
    expectSummaryAndHints(normalized)
  })

  test("normalizes tool_call", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("tool_call", {
        call: {
          id: "tc-1",
          name: "apply_patch",
          arguments: "{\"path\":\"a.ts\"}",
        },
      }),
    )

    expect(normalized.type).toBe("tool.call")
    expect(normalized.actor?.kind).toBe("tool")
    expect(normalized.toolCallId).toBe("tc-1")
    expect(normalized.toolName).toBe("apply_patch")
    expect(normalized.toolClass).toBe("default")
    expect(normalized.argsText).toBe("{\"path\":\"a.ts\"}")
    expect(normalized.summary.title).toBe("Tool call")
    expect(normalized.summary.short).toBe("call apply_patch")
    expect(normalized.hints.lane).toBe("tool")
    expect(normalized.hints.tone).toBe("info")
    expectSummaryAndHints(normalized)
  })

  test("normalizes tool_result", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("tool_result", {
        summary: "patch applied",
        result: {
          id: "tc-1",
          name: "apply_patch",
          ok: true,
        },
      }),
    )

    expect(normalized.type).toBe("tool.result")
    expect(normalized.actor?.kind).toBe("tool")
    expect(normalized.toolCallId).toBe("tc-1")
    expect(normalized.toolName).toBe("apply_patch")
    expect(normalized.toolClass).toBe("default")
    expect(normalized.ok).toBe(true)
    expect(normalized.summary.title).toBe("Tool result")
    expect(normalized.summary.short).toBe("apply_patch succeeded")
    expect(normalized.summary.detail).toBe("patch applied")
    expect(normalized.hints.lane).toBe("tool")
    expect(normalized.hints.tone).toBe("success")
    expectSummaryAndHints(normalized)
  })

  test("normalizes permission_request and permission_response", () => {
    const request = normalizeBridgeEvent(
      mkEvent("permission_request", {
        request_id: "req-1",
        tool: "shell_command",
      }),
    )
    const response = normalizeBridgeEvent(
      mkEvent("permission_response", {
        request_id: "req-1",
        decision: "allow",
      }),
    )

    expect(request.type).toBe("permission.request")
    expect(request.requestId).toBe("req-1")
    expect(request.summary.short).toBe("request req-1")
    expect(request.summary.detail).toBe("for shell_command")
    expect(request.hints).toEqual({
      lane: "permission",
      badge: "permission",
      tone: "warning",
      priority: "high",
      stream: false,
    })

    expect(response.type).toBe("permission.response")
    expect(response.requestId).toBe("req-1")
    expect(response.decision).toBe("allow")
    expect(response.summary.short).toBe("request req-1: allow")
    expect(response.hints.lane).toBe("permission")
    expect(response.hints.tone).toBe("success")
    expectSummaryAndHints(request)
    expectSummaryAndHints(response)
  })

  test("normalizes run_finished", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("run_finished", {
        completed: true,
        reason: "replay",
        eventCount: 8,
      }),
    )

    expect(normalized.type).toBe("run.finished")
    expect(normalized.ok).toBe(true)
    expect(normalized.summary.title).toBe("Run finished")
    expect(normalized.summary.short).toBe("run completed: replay")
    expect(normalized.summary.detail).toBe("reason=replay events=8")
    expect(normalized.hints.badge).toBe("run")
    expect(normalized.hints.tone).toBe("success")
    expect(normalized.overlayIntent).toEqual({ kind: "task", action: "close" })
    expectSummaryAndHints(normalized)
  })

  test("normalizes task_event", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("task_event", {
        task_id: "t-1",
        status: "running",
        description: "Index shard",
        lane_id: "lane-1",
        lane_label: "Indexer",
      }),
    )
    expect(normalized.type).toBe("task.update")
    expect(normalized.taskId).toBe("t-1")
    expect(normalized.taskStatus).toBe("running")
    expect(normalized.taskLaneId).toBe("lane-1")
    expect(normalized.taskSubagentId).toBe("lane-1")
    expect(normalized.taskSubagentLabel).toBe("Indexer")
    expect(normalized.summary.short).toContain("running")
    expect(normalized.hints.badge).toBe("task")
    expect(normalized.overlayIntent).toEqual({ kind: "task", action: "update", taskId: "t-1" })
  })

  test("prefers explicit child/parent session ids for task graph", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("task_event", {
        task_id: "t-child-1",
        status: "running",
        description: "Run child job",
        parent_session_id: "sess-parent-1",
        child_session_id: "sess-child-9",
        child_session_label: "Subagent-9",
        lane_id: "lane-fallback",
      }),
    )
    expect(normalized.taskSubagentId).toBe("sess-child-9")
    expect(normalized.childSessionId).toBe("sess-child-9")
    expect(normalized.parentSessionId).toBe("sess-parent-1")
    expect(normalized.taskSubagentLabel).toBe("Subagent-9")
  })

  test("normalizes warning", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("warning", {
        message: "resize 160x45 -> 120x34",
      }),
    )
    expect(normalized.type).toBe("warning")
    expect(normalized.summary.short).toContain("resize")
    expect(normalized.hints.badge).toBe("warning")
    expect(normalized.hints.tone).toBe("warning")
  })

  test("classifies context-collection tool names and uses compact policy by default", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("tool_call", {
        call: {
          id: "tc-read-1",
          name: "read_file",
          arguments: JSON.stringify({ path: "README.md", from: 1, to: 200 }),
        },
      }),
    )
    expect(normalized.type).toBe("tool.call")
    expect(normalized.toolName).toBe("read_file")
    expect(normalized.toolClass).toBe("context_collection")
    expect(normalized.toolRenderPolicy).toEqual({
      mode: "compact",
      reason: "context-collection",
    })
  })

  test("context-collection tool results expand only when very large", () => {
    const hugeBlob = "x".repeat(1300)
    const normalized = normalizeBridgeEvent(
      mkEvent("tool_result", {
        result: {
          id: "tc-read-2",
          name: "read_file",
          ok: true,
          output: hugeBlob,
        },
      }),
    )
    expect(normalized.type).toBe("tool.result")
    expect(normalized.toolClass).toBe("context_collection")
    expect(normalized.toolRenderPolicy).toEqual({
      mode: "expanded",
      reason: "context-collection-result-huge",
    })
  })

  test("artifact-backed diff tool results prefer expanded artifact rendering", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("tool_result", {
        tool: "apply_patch",
        ok: true,
        display: {
          detail_artifact: {
            schema_version: "artifact_ref_v1",
            id: "artifact-diff-1",
            kind: "tool_diff",
            mime: "text/x-diff",
            path: "docs_tmp/tool_diff.diff",
            size_bytes: 512,
            preview: {
              lines: ["diff --git a/a.ts b/a.ts", "@@ -1 +1 @@"],
              note: "Large unified diff exported to artifact.",
            },
          },
        },
      }),
    )

    expect(normalized.type).toBe("tool.result")
    expect(normalized.artifactRef?.kind).toBe("tool_diff")
    expect(normalized.toolRenderPolicy).toEqual({
      mode: "expanded",
      reason: "artifact-diff",
    })
    const rendered = formatNormalizedEventForStdout(normalized) ?? ""
    expect(rendered).toContain("(ok, artifact)")
    expect(rendered).toContain("artifact: tool_diff")
    expect(rendered).toContain("preview:")
    expect(rendered).toContain("Large unified diff exported to artifact.")
  })

  test("normalizes error", () => {
    const normalized = normalizeBridgeEvent(
      mkEvent("error", {
        message: "bridge exploded",
        code: "E_BRIDGE",
      }),
    )

    expect(normalized.type).toBe("error")
    expect(normalized.error).toEqual({ message: "bridge exploded", code: "E_BRIDGE" })
    expect(normalized.summary.title).toBe("Error")
    expect(normalized.summary.short).toBe("bridge exploded")
    expect(normalized.summary.detail).toBe("code=E_BRIDGE")
    expect(normalized.hints.badge).toBe("error")
    expect(normalized.hints.tone).toBe("danger")
    expectSummaryAndHints(normalized)
  })

  test("falls back for unknown events with deterministic summary", () => {
    const unknownEvent = mkEvent("mystery_event", {
      z: 1,
      a: 2,
    })

    const first = normalizeBridgeEvent(unknownEvent)
    const second = normalizeBridgeEvent(unknownEvent)

    expect(first.type).toBe("unknown")
    expect(first.rawType).toBe("mystery_event")
    expect(first.summary.title).toBe("mystery_event")
    expect(first.summary.short).toBe('{"a":2,"z":1}')
    expect(first.hints.badge).toBe("mystery_event")
    expect(first.hints.tone).toBe("neutral")
    expect(first).toEqual(second)
    expectSummaryAndHints(first)
  })
})

describe("deriveSlabDisplayHints", () => {
  test("uses decision-aware tone for permission responses", () => {
    expect(deriveSlabDisplayHints("permission.response", { decision: "deny" }).tone).toBe("danger")
    expect(deriveSlabDisplayHints("permission.response", { decision: "allow" }).tone).toBe("success")
    expect(deriveSlabDisplayHints("permission.response", { decision: "unknown" }).tone).toBe("neutral")
  })
})

describe("classifyToolName", () => {
  test("matches known context-collection tools and prefixes", () => {
    expect(classifyToolName("read_file")).toBe("context_collection")
    expect(classifyToolName("grep")).toBe("context_collection")
    expect(classifyToolName("search_codebase")).toBe("context_collection")
    expect(classifyToolName("apply_patch")).toBe("default")
    expect(classifyToolName("")).toBe("default")
    expect(classifyToolName(undefined)).toBe("default")
  })
})
