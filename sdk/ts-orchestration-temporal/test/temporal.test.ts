import test from "node:test"
import assert from "node:assert/strict"

import type { DistributedTaskDescriptorV1 } from "@breadboard/kernel-contracts"
import { buildTemporalTaskQueue, buildTemporalWorkflowStartDescriptor } from "../src/index.js"

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
