import test from "node:test"
import assert from "node:assert/strict"

import {
  buildKernelEventId,
  buildTaskLineage,
  cloneCheckpointMetadata,
  cloneTranscriptItem,
  eventBelongsToSession,
  loadEngineConformanceManifest,
  loadKernelFixture,
  normalizeTranscriptContractItem,
} from "../src/index.js"

test("kernel core helpers remain deterministic", () => {
  assert.equal(buildKernelEventId("run-1", 7), "run-1:evt:7")

  const original = {
    kind: "assistant_message",
    visibility: "model",
    content: { text: "hello" },
  } as const
  const cloned = cloneTranscriptItem(original)
  assert.deepEqual(cloned, original)
  assert.notEqual(cloned, original)

  const event = {
    schemaVersion: "bb.kernel_event.v1",
    eventId: "evt-1",
    runId: "run-1",
    sessionId: "sess-1",
    seq: 1,
    ts: "2026-03-08T00:00:00Z",
    actor: "engine",
    visibility: "model",
    kind: "assistant_message",
    payload: {},
  } as const
  assert.equal(eventBelongsToSession(event, "sess-1"), true)
  assert.equal(eventBelongsToSession(event, "sess-2"), false)
})

test("kernel core transcript normalization maps legacy entries", () => {
  const normalized = normalizeTranscriptContractItem({ assistant: "hello" })
  assert.deepEqual(normalized, {
    kind: "assistant_message",
    visibility: "model",
    content: "hello",
    provenance: { source: "legacy_transcript_entry", legacy_key: "assistant" },
  })
})

test("kernel core lineage and checkpoint helpers stay deterministic", () => {
  const lineage = buildTaskLineage(
    {
      schema_version: "bb.task.v1",
      task_id: "child",
      kind: "subagent_spawned",
      status: "running",
      parent_task_id: "root",
    },
    {
      root: {
        schema_version: "bb.task.v1",
        task_id: "root",
        kind: "root",
        status: "running",
      },
    },
  )
  assert.deepEqual(lineage, ["root", "child"])

  const checkpoint = cloneCheckpointMetadata({
    schema_version: "bb.checkpoint_metadata.v1",
    source_kind: "workspace_checkpoint",
    checkpoint_ref: "ckpt-1",
    created_at: 1,
    summary: { preview: "test" },
  })
  assert.equal(checkpoint.summary.preview, "test")
})

test("kernel core can load tracked manifest and fixtures", () => {
  const manifest = loadEngineConformanceManifest()
  assert.equal(manifest.schemaVersion, "bb.engine_conformance_manifest.v1")
  assert.ok(manifest.rows.length >= 1)

  const fixture = loadKernelFixture<{ fixture_id: string }>("kernel_event/reference_fixture.json")
  assert.equal(fixture.fixture_id, "kernel_event_python_reference")
})
