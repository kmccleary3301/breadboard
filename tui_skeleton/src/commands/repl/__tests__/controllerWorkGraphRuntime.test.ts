import { describe, expect, it } from "vitest"
import {
  createWorkGraphState,
  formatWorkGraphReducerTrace,
  reduceWorkGraphEvent,
  reduceWorkGraphEvents,
  resolveWorkGraphLimits,
  type WorkGraphReduceInput,
} from "../controllerWorkGraphRuntime.js"

describe("controllerWorkGraphRuntime", () => {
  it("builds deterministic state from task_event payloads", () => {
    const initial = createWorkGraphState()
    const next = reduceWorkGraphEvent(initial, {
      eventType: "task_event",
      eventId: "evt-1",
      seq: 1,
      payload: {
        task_id: "task-1",
        status: "running",
        description: "Index repository",
        subagent_type: "research",
      },
    })

    expect(next.itemOrder).toEqual(["task-1"])
    const item = next.itemsById["task-1"]
    expect(item).toBeTruthy()
    expect(item.status).toBe("running")
    expect(item.title).toBe("Index repository")
    expect(item.laneLabel).toBe("research")
    expect(next.laneOrder).toEqual(["task:task-1"])
    expect(next.lanesById["task:task-1"]?.statusSummary.running).toBe(1)
  })

  it("is idempotent when the same event key is replayed", () => {
    const initial = createWorkGraphState()
    const event: WorkGraphReduceInput = {
      eventType: "task_event",
      eventId: "evt-dup",
      seq: 7,
      payload: {
        task_id: "task-dup",
        status: "running",
        kind: "tool_call",
        tool: "bash",
        call_id: "call-1",
      },
    }
    const once = reduceWorkGraphEvent(initial, event)
    const twice = reduceWorkGraphEvent(once, event)
    expect(twice).toEqual(once)
    expect(twice.itemsById["task-dup"]?.steps.length).toBe(1)
  })

  it("normalizes out-of-order events when reducing a batch", () => {
    const initial = createWorkGraphState()
    const outOfOrder: WorkGraphReduceInput[] = [
      {
        eventType: "task_event",
        seq: 3,
        payload: { task_id: "task-2", status: "completed", description: "done" },
      },
      {
        eventType: "task_event",
        seq: 1,
        payload: { task_id: "task-2", status: "running", description: "start" },
      },
      {
        eventType: "task_event",
        seq: 2,
        payload: { task_id: "task-2", status: "blocked", description: "wait" },
      },
    ]
    const reduced = reduceWorkGraphEvents(initial, outOfOrder)
    expect(reduced.itemsById["task-2"]?.status).toBe("completed")
    expect(reduced.lastSeq).toBe(3)
  })

  it("enforces maxWorkItems eviction deterministically", () => {
    const initial = createWorkGraphState()
    const limits = resolveWorkGraphLimits({ maxWorkItems: 2, maxProcessedEventKeys: 32 })
    const withOne = reduceWorkGraphEvent(initial, {
      eventType: "task_event",
      seq: 1,
      eventId: "ev-1",
      payload: { task_id: "task-a", status: "running" },
    }, limits)
    const withTwo = reduceWorkGraphEvent(withOne, {
      eventType: "task_event",
      seq: 2,
      eventId: "ev-2",
      payload: { task_id: "task-b", status: "running" },
    }, limits)
    const withThree = reduceWorkGraphEvent(withTwo, {
      eventType: "task_event",
      seq: 3,
      eventId: "ev-3",
      payload: { task_id: "task-c", status: "running" },
    }, limits)
    expect(withThree.itemOrder.length).toBe(2)
    expect(withThree.itemsById["task-a"]).toBeUndefined()
    expect(withThree.itemsById["task-c"]).toBeTruthy()
    expect(withThree.itemsById["task-b"]).toBeTruthy()
  })

  it("models sync task progression with deterministic counters", () => {
    const initial = createWorkGraphState()
    const reduced = reduceWorkGraphEvents(initial, [
      {
        eventType: "task_event",
        seq: 1,
        payload: {
          task_id: "task-sync",
          mode: "sync",
          status: "running",
          kind: "tool_call",
          tool: "read_file",
          call_id: "sync-call",
        },
      },
      {
        eventType: "task_event",
        seq: 2,
        payload: {
          task_id: "task-sync",
          mode: "sync",
          status: "completed",
          kind: "tool_call",
          tool: "read_file",
          call_id: "sync-call",
        },
      },
    ])

    const item = reduced.itemsById["task-sync"]
    expect(item?.mode).toBe("sync")
    expect(item?.status).toBe("completed")
    expect(item?.counters.total).toBe(1)
    expect(item?.counters.completed).toBe(1)
  })

  it("formats a compact reducer trace summary", () => {
    const initial = createWorkGraphState()
    const events: WorkGraphReduceInput[] = [
      {
        eventType: "task_event",
        seq: 1,
        payload: { task_id: "task-t", status: "running" },
      },
    ]
    const reduced = reduceWorkGraphEvents(initial, events)
    const trace = formatWorkGraphReducerTrace(initial, reduced, events)
    expect(trace).toContain("[workgraph-trace]")
    expect(trace).toContain("events=1")
    expect(trace).toContain("seq=1..1")
    expect(trace).toContain("items=0->1")
  })

  it("enforces maxStepsPerTask eviction deterministically", () => {
    const initial = createWorkGraphState()
    const limits = resolveWorkGraphLimits({ maxStepsPerTask: 3, maxProcessedEventKeys: 128 })
    let state = initial
    for (let seq = 1; seq <= 5; seq += 1) {
      state = reduceWorkGraphEvent(
        state,
        {
          eventType: "task_event",
          seq,
          eventId: `evt-step-${seq}`,
          payload: {
            task_id: "task-steps",
            status: "running",
            kind: "tool_call",
            tool: "bash",
            call_id: `call-${seq}`,
            detail: `step ${seq}`,
          },
        },
        limits,
      )
    }
    const steps = state.itemsById["task-steps"]?.steps ?? []
    expect(steps.length).toBe(3)
    expect(steps.map((step) => step.stepId)).toEqual(["call-3", "call-4", "call-5"])
  })

  it("tracks failure -> retry -> completion for the same task", () => {
    const initial = createWorkGraphState()
    const reduced = reduceWorkGraphEvents(initial, [
      {
        eventType: "task_event",
        seq: 1,
        eventId: "evt-retry-1",
        payload: {
          task_id: "task-retry",
          status: "failed",
          kind: "tool_call",
          tool: "worker",
          call_id: "call-retry",
          attempt: 1,
          error: "timeout waiting for output",
        },
      },
      {
        eventType: "task_event",
        seq: 2,
        eventId: "evt-retry-2",
        payload: {
          task_id: "task-retry",
          status: "running",
          kind: "tool_call",
          tool: "worker",
          call_id: "call-retry",
          attempt: 2,
        },
      },
      {
        eventType: "task_event",
        seq: 3,
        eventId: "evt-retry-3",
        payload: {
          task_id: "task-retry",
          status: "completed",
          kind: "tool_call",
          tool: "worker",
          call_id: "call-retry",
          attempt: 2,
        },
      },
    ])

    const item = reduced.itemsById["task-retry"]
    expect(item?.status).toBe("completed")
    expect(item?.steps.length).toBe(1)
    expect(item?.steps[0]?.attempt).toBe(2)
    expect(item?.counters.completed).toBe(1)
  })

  it("applies checklist transitions pending -> running -> completed", () => {
    const initial = createWorkGraphState()
    const reduced = reduceWorkGraphEvents(initial, [
      {
        eventType: "task_event",
        seq: 1,
        payload: {
          task_id: "task-checklist",
          status: "pending",
          kind: "tool_call",
          tool: "plan",
          call_id: "check-call",
        },
      },
      {
        eventType: "task_event",
        seq: 2,
        payload: {
          task_id: "task-checklist",
          status: "running",
          kind: "tool_call",
          tool: "plan",
          call_id: "check-call",
        },
      },
      {
        eventType: "task_event",
        seq: 3,
        payload: {
          task_id: "task-checklist",
          status: "completed",
          kind: "tool_call",
          tool: "plan",
          call_id: "check-call",
        },
      },
    ])

    const step = reduced.itemsById["task-checklist"]?.steps[0]
    expect(step?.status).toBe("completed")
    expect(step?.startedAt).toBe(2)
    expect(step?.endedAt).toBe(3)
    expect(reduced.itemsById["task-checklist"]?.counters.completed).toBe(1)
  })

  it("remains bounded and deterministic for a 20-task async burst", () => {
    const initial = createWorkGraphState()
    const events: WorkGraphReduceInput[] = []
    for (let index = 0; index < 20; index += 1) {
      events.push({
        eventType: "task_event",
        seq: index + 1,
        eventId: `evt-burst-${index + 1}`,
        payload: {
          task_id: `task-burst-${index + 1}`,
          status: "running",
          mode: "async",
          lane_id: `lane-${(index % 4) + 1}`,
          kind: "tool_call",
          tool: "worker",
          call_id: `call-${index + 1}`,
        },
      })
    }

    const reduced = reduceWorkGraphEvents(initial, events, resolveWorkGraphLimits({ maxWorkItems: 20 }))
    expect(reduced.itemOrder.length).toBe(20)
    expect(reduced.laneOrder.length).toBe(4)
    for (const workId of reduced.itemOrder) {
      expect(reduced.itemsById[workId]?.status).toBe("running")
    }
  })
})
