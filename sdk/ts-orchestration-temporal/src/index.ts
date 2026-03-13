import {
  assertValid,
  type DistributedTaskDescriptorV1,
  type TranscriptContinuationPatchV1,
  type WakeSubscriptionV1,
} from "@breadboard/kernel-contracts"

export interface TemporalWorkflowStartDescriptor {
  workflowId: string
  workflowType: string
  taskQueue: string
  searchAttributes: Record<string, unknown>
  memo: Record<string, unknown>
  retryPolicy: Record<string, unknown> | null
  input: Record<string, unknown>
}

export interface TemporalWorkflowSignalDescriptor {
  signalName: string
  payload: Record<string, unknown>
}

export interface TemporalWorkflowUpdateDescriptor {
  updateName: string
  payload: Record<string, unknown>
}

export interface TemporalTaskControlPlaneDescriptor {
  signalDescriptors: TemporalWorkflowSignalDescriptor[]
  updateDescriptors: TemporalWorkflowUpdateDescriptor[]
  queryNames: string[]
}

export interface TemporalResumeUpdateDescriptor {
  workflowId: string
  updateName: "breadboard.resume"
  payload: Record<string, unknown>
}

export interface CoordinationResumeMetadata {
  subscriptionId?: string | null
  triggerSignalId?: string | null
  triggerCode?: string | null
  cursorEventId?: number | null
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

function normalizeDistributedTaskDescriptor(
  descriptorInput: DistributedTaskDescriptorV1,
): DistributedTaskDescriptorV1 {
  return assertValid<DistributedTaskDescriptorV1>("distributedTaskDescriptor", descriptorInput)
}

function normalizeWakeSubscriptions(descriptor: DistributedTaskDescriptorV1): WakeSubscriptionV1[] {
  return (descriptor.wake_subscriptions ?? []).map((subscription) =>
    assertValid<WakeSubscriptionV1>("wakeSubscription", subscription),
  )
}

export function buildTemporalWorkflowStartDescriptor(
  descriptorInput: DistributedTaskDescriptorV1,
): TemporalWorkflowStartDescriptor {
  const descriptor = normalizeDistributedTaskDescriptor(descriptorInput)
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
      wakeSubscriptions: normalizeWakeSubscriptions(descriptor),
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

export function buildTemporalTaskControlPlaneDescriptor(
  descriptorInput: DistributedTaskDescriptorV1,
): TemporalTaskControlPlaneDescriptor {
  const descriptor = normalizeDistributedTaskDescriptor(descriptorInput)
  const wakeSubscriptions = normalizeWakeSubscriptions(descriptor)
  const signalDescriptors: TemporalWorkflowSignalDescriptor[] = []

  if (wakeSubscriptions.length > 0) {
    signalDescriptors.push({
      signalName: "breadboard.coordinationSignal",
      payload: {
        taskId: descriptor.task_id,
        wakeSubscriptions,
      },
    })
  } else {
    signalDescriptors.push({
      signalName: "breadboard.wake",
      payload: {
        taskId: descriptor.task_id,
        wakeConditions: descriptor.wake_conditions ?? [],
      },
    })
  }

  if ((descriptor.wake_conditions ?? []).includes("child_complete")) {
    signalDescriptors.push({
      signalName: "breadboard.childComplete",
      payload: {
        taskId: descriptor.task_id,
        parentTaskId: descriptor.parent_task_id ?? null,
      },
    })
  }
  const timerWakeConditions = (descriptor.wake_conditions ?? []).filter((condition) => condition.startsWith("timer:"))
  if (timerWakeConditions.length > 0) {
    signalDescriptors.push({
      signalName: "breadboard.timerWake",
      payload: {
        taskId: descriptor.task_id,
        timers: timerWakeConditions,
      },
    })
  }

  const updateDescriptors: TemporalWorkflowUpdateDescriptor[] = [
    {
      updateName: "breadboard.recordCheckpoint",
      payload: {
        taskId: descriptor.task_id,
        checkpointStrategy: descriptor.checkpoint_strategy ?? null,
      },
    },
    {
      updateName: "breadboard.appendArtifactRefs",
      payload: {
        taskId: descriptor.task_id,
        artifactRefs: descriptor.artifact_refs ?? [],
      },
    },
  ]

  return {
    signalDescriptors,
    updateDescriptors,
    queryNames: ["breadboard.getState", "breadboard.getCheckpoint", "breadboard.getLineage"],
  }
}

export function buildTemporalResumeUpdateDescriptor(input: {
  descriptor: DistributedTaskDescriptorV1
  transcriptPatch?: TranscriptContinuationPatchV1 | null
  resumeReason?: string | null
  coordination?: CoordinationResumeMetadata | null
}): TemporalResumeUpdateDescriptor {
  const descriptor = normalizeDistributedTaskDescriptor(input.descriptor)
  const transcriptPatch =
    input.transcriptPatch != null
      ? assertValid<TranscriptContinuationPatchV1>("transcriptContinuationPatch", input.transcriptPatch)
      : null
  const coordination = input.coordination ?? null
  return {
    workflowId: descriptor.task_id,
    updateName: "breadboard.resume",
    payload: {
      taskId: descriptor.task_id,
      taskKind: descriptor.task_kind,
      resumeReason: input.resumeReason ?? "host_resume",
      checkpointStrategy: descriptor.checkpoint_strategy ?? null,
      coordination: coordination
        ? {
            subscriptionId: coordination.subscriptionId ?? null,
            triggerSignalId: coordination.triggerSignalId ?? null,
            triggerCode: coordination.triggerCode ?? null,
            cursorEventId: coordination.cursorEventId ?? null,
          }
        : null,
      transcriptPatch,
    },
  }
}
