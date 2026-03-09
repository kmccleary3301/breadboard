import { assertValid, type DistributedTaskDescriptorV1 } from "@breadboard/kernel-contracts"

export interface TemporalWorkflowStartDescriptor {
  workflowId: string
  workflowType: string
  taskQueue: string
  searchAttributes: Record<string, unknown>
  memo: Record<string, unknown>
  retryPolicy: Record<string, unknown> | null
  input: Record<string, unknown>
}

export function buildTemporalTaskQueue(task: DistributedTaskDescriptorV1): string {
  switch (task.task_kind) {
    case "subagent":
      return "breadboard-subagents"
    case "background":
      return "breadboard-background"
    case "workflow":
      return "breadboard-workflows"
    default:
      return "breadboard-default"
  }
}

export function buildTemporalWorkflowStartDescriptor(
  descriptorInput: DistributedTaskDescriptorV1,
): TemporalWorkflowStartDescriptor {
  const descriptor = assertValid<DistributedTaskDescriptorV1>("distributedTaskDescriptor", descriptorInput)
  return {
    workflowId: descriptor.task_id,
    workflowType: `breadboard.${descriptor.task_kind}`,
    taskQueue: buildTemporalTaskQueue(descriptor),
    searchAttributes: {
      taskKind: descriptor.task_kind,
      parentTaskId: descriptor.parent_task_id ?? null,
      expectedOutputContract: descriptor.expected_output_contract ?? null,
    },
    memo: {
      placementPreferences: descriptor.placement_preferences ?? [],
      wakeConditions: descriptor.wake_conditions ?? [],
      joinPolicy: descriptor.join_policy ?? null,
      checkpointStrategy: descriptor.checkpoint_strategy ?? null,
      artifactRefs: descriptor.artifact_refs ?? [],
    },
    retryPolicy: descriptor.retry_policy ?? null,
    input: {
      taskId: descriptor.task_id,
      taskKind: descriptor.task_kind,
      budget: descriptor.budget ?? null,
      priority: descriptor.priority ?? null,
    },
  }
}
