import test from "node:test"
import assert from "node:assert/strict"

import type { DistributedTaskDescriptorV1, TranscriptContinuationPatchV1 } from "@breadboard/kernel-contracts"
import {
  buildTemporalResumeUpdateDescriptor,
  buildTemporalTaskControlPlaneDescriptor,
  buildTemporalTaskQueue,
  buildTemporalWorkflowStartDescriptor,
} from "../src/index.js"

const backgroundTask: DistributedTaskDescriptorV1 = {
  schema_version: "bb.distributed_task_descriptor.v1",
  task_id: "task:bg:1",
  task_kind: "background",
  parent_task_id: "task:root:1",
  placement_preferences: ["remote_worker", "delegated_oci"],
  checkpoint_strategy: "every_step",
  wake_conditions: ["child_complete", "timer:30s"],
  join_policy: "all_children",
  retry_policy: { max_attempts: 3 },
  priority: 2,
  budget: { wall_ms: 60000 },
  expected_output_contract: "bb.run_context.v1",
  artifact_refs: ["artifact://task/bg/1"],
}

const transcriptPatch: TranscriptContinuationPatchV1 = {
  schema_version: "bb.transcript_continuation_patch.v1",
  patch_id: "patch:bg:1",
  pre_state_ref: "transcript://session/bg/1#state",
  appended_messages: [
    {
      role: "assistant",
      text: "continuing after checkpoint",
    },
  ],
  post_state_digest: "sha256:temporal-patch-1",
  lineage_updates: [{ task_id: "task:bg:1", status: "resumed" }],
  lossiness_flags: [],
}

test("temporal adapter derives task queues from task kind", () => {
  assert.equal(buildTemporalTaskQueue(backgroundTask), "breadboard-background")
})

test("temporal adapter maps distributed task descriptor into a start descriptor", () => {
  const descriptor = buildTemporalWorkflowStartDescriptor(backgroundTask)
  assert.equal(descriptor.workflowId, backgroundTask.task_id)
  assert.equal(descriptor.workflowType, "breadboard.background")
  assert.equal(descriptor.taskQueue, "breadboard-background")
  assert.equal(descriptor.searchAttributes.parentTaskId, "task:root:1")
  assert.deepEqual(descriptor.memo.wakeConditions, ["child_complete", "timer:30s"])
  assert.deepEqual(descriptor.retryPolicy, { max_attempts: 3 })
})

test("temporal adapter derives workflow control-plane descriptors", () => {
  const controlPlane = buildTemporalTaskControlPlaneDescriptor(backgroundTask)
  assert.equal(controlPlane.signalDescriptors[0]?.signalName, "breadboard.wake")
  assert.equal(controlPlane.signalDescriptors[1]?.signalName, "breadboard.childComplete")
  assert.equal(controlPlane.signalDescriptors[2]?.signalName, "breadboard.timerWake")
  assert.deepEqual(controlPlane.queryNames, [
    "breadboard.getState",
    "breadboard.getCheckpoint",
    "breadboard.getLineage",
  ])
  assert.equal(controlPlane.updateDescriptors[0]?.updateName, "breadboard.recordCheckpoint")
  assert.equal(controlPlane.updateDescriptors[1]?.updateName, "breadboard.appendArtifactRefs")
})

test("temporal adapter builds a resume update descriptor with transcript continuation", () => {
  const descriptor = buildTemporalResumeUpdateDescriptor({
    descriptor: backgroundTask,
    transcriptPatch,
    resumeReason: "timer_wake",
  })
  assert.equal(descriptor.workflowId, backgroundTask.task_id)
  assert.equal(descriptor.updateName, "breadboard.resume")
  assert.equal(descriptor.payload.taskId, backgroundTask.task_id)
  assert.equal(descriptor.payload.resumeReason, "timer_wake")
  assert.deepEqual(descriptor.payload.transcriptPatch, transcriptPatch)
})
