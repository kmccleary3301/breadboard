import { describe, expect, it } from "vitest"
import { ReplSessionController } from "../controller.js"

const event = (seq: number, type: string, payload: Record<string, unknown> = {}) => ({
  id: String(seq),
  seq,
  turn: 1,
  type,
  payload,
})

const ctreeSnapshot = {
  schema_version: "1",
  node_count: 1,
  event_count: 1,
  last_id: "node-1",
  node_hash: null,
}

describe("ReplSessionController ctree transcript notices", () => {
  it("keeps streaming-disabled transcript notices out of the readable transcript without terminating the run", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "ctree_node", {
      node: {
        id: "node-1",
        payload: {
          transcript: {
            streaming_disabled: {
              provider: "openai",
              runtime: "openai_responses",
              reason: "You exceeded your current quota.",
            },
          },
        },
      },
      snapshot: ctreeSnapshot,
    }))

    const state = controller.getState()
    expect(state.pendingResponse).toBe(true)
    expect(state.activity?.primary).toBe("thinking")
    expect(state.hints.some((hint: string) => hint.includes("Streaming fallback active"))).toBe(false)
    expect(state.toolEvents.some((entry: any) => String(entry.text).includes("Streaming fallback active"))).toBe(false)
    expect(state.toolEvents.some((entry: any) => String(entry.text).includes("response.keep_alive"))).toBe(false)
  })

  it("does not surface repeated streaming-disabled transcript notices", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    const payload = {
      node: {
        id: "node-1",
        payload: {
          transcript: {
            streaming_disabled: {
              provider: "openai",
              runtime: "openai_responses",
              reason: "Expected to have received `response.created` before `response.keep_alive`",
            },
          },
        },
      },
      snapshot: ctreeSnapshot,
    }
    controller.applyEvent(event(2, "ctree_node", payload))
    controller.applyEvent(event(3, "ctree_node", payload))

    const state = controller.getState()
    const warnings = state.toolEvents.filter((entry: any) => String(entry.text).includes("Streaming fallback active"))
    expect(warnings).toHaveLength(0)
    expect(state.hints.some((hint: string) => hint.includes("Streaming fallback active"))).toBe(false)
  })

  it("surfaces run-loop exceptions from direct ctree node payloads as visible terminal errors", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "ctree_node", {
      node: {
        id: "node-1",
        payload: {
          run_loop_exception: {
            type: "ProviderRuntimeError",
            message: "Error code: 429 - insufficient_quota",
          },
        },
      },
      snapshot: ctreeSnapshot,
    }))

    const state = controller.getState()
    expect(state.pendingResponse).toBe(false)
    expect(state.activity?.primary).toBe("error")
    expect(state.hints.some((hint: string) => hint.includes("[error] Error code: 429 - insufficient_quota"))).toBe(true)
    expect(state.conversation.some((entry: any) => entry.speaker === "system" && String(entry.text).includes("[error] Error code: 429 - insufficient_quota"))).toBe(true)
    expect(state.guardrailNotice?.summary).toContain("Error: Error code: 429 - insufficient_quota")
  })

  it("surfaces failed completion summaries as visible terminal errors", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "completion", {
      summary: {
        completed: false,
        reason: "run_loop_exception",
        error: {
          message: "Error code: 429 - insufficient_quota",
          traceback: "Traceback (most recent call last):\nProviderRuntimeError: quota",
        },
      },
    }))

    const state = controller.getState()
    expect(state.pendingResponse).toBe(false)
    expect(state.activity?.primary).toBe("halted")
    expect(state.hints.some((hint: string) => hint.includes("✻ Cooked for") || hint.includes("✻ Halted"))).toBe(false)
    expect(state.hints.some((hint: string) => hint.includes("[error] Error code: 429 - insufficient_quota"))).toBe(true)
    expect(state.conversation.some((entry: any) => entry.speaker === "system" && String(entry.text).includes("[error] Error code: 429 - insufficient_quota"))).toBe(true)
    const errorEntry = state.toolEvents.find((entry: any) => entry.kind === "error")
    expect(errorEntry?.text).toContain("[error] Error code: 429 - insufficient_quota")
    expect(errorEntry?.display?.title).toBe("Runtime error")
    expect(errorEntry?.display?.detail?.join("\n") ?? "").toContain("ProviderRuntimeError: quota")
    expect(state.guardrailNotice?.summary).toContain("Error: Error code: 429 - insufficient_quota")
  })

  it("surfaces non-exception workloop halts as readable system transcript rows", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "completion", {
      summary: {
        completed: false,
        reason: "implementation_missing_write_receipt_loop",
        method: "workloop_guard",
      },
    }))

    const state = controller.getState()
    expect(state.pendingResponse).toBe(false)
    expect(state.activity?.primary).toBe("halted")
    expect(
      state.conversation.some(
        (entry: any) =>
          entry.speaker === "system" && String(entry.text).includes("[halted] implementation_missing_write_receipt_loop"),
      ),
    ).toBe(true)
  })

  it("surfaces run-loop exceptions from ctree transcript nodes as visible terminal errors", () => {
    const controller = new ReplSessionController({
      configPath: "agent_configs/misc/test_simple_native.yaml",
      workspace: ".",
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
    }

    controller.applyEvent(event(1, "run.start"))
    controller.applyEvent(event(2, "ctree_node", {
      node: {
        id: "node-1",
        payload: {
          transcript: {
            run_loop_exception: {
              type: "ProviderRuntimeError",
              message: "You exceeded your current quota.",
              traceback: "Traceback (most recent call last):\n  File \"runner.py\", line 1, in <module>\nProviderRuntimeError: quota",
            },
          },
        },
      },
      snapshot: ctreeSnapshot,
    }))

    const state = controller.getState()
    expect(state.pendingResponse).toBe(false)
    expect(state.activity?.primary).toBe("error")
    expect(state.hints.some((hint: string) => hint.includes("[error] You exceeded your current quota."))).toBe(true)
    const errorEntry = state.toolEvents.find((entry: any) => entry.kind === "error")
    expect(errorEntry?.text).toContain("[error] You exceeded your current quota.")
    expect(errorEntry?.display?.title).toBe("Runtime error")
    expect(errorEntry?.display?.detail?.join("\n") ?? "").toContain("ProviderRuntimeError: quota")
    expect(state.conversation.some((entry: any) => entry.speaker === "system" && String(entry.text).includes("[error] You exceeded your current quota."))).toBe(true)
    expect(state.guardrailNotice?.summary).toContain("Error: You exceeded your current quota.")
  })
})
