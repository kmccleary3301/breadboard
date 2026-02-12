import { describe, expect, it } from "vitest"
import {
  createWorkGraphState,
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
})

