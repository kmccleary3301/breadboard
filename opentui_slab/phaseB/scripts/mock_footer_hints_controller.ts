import { createIpcServer } from "../ipc.ts"
import { nowEnvelope, IPC_PROTOCOL_VERSION } from "../protocol.ts"

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const main = async () => {
  const server = await createIpcServer({ host: "127.0.0.1" })
  // Easy-to-parse line for shell scripts.
  console.error(`IPC_HOST=${server.host} IPC_PORT=${server.port}`)

  server.onConnection((conn) => {
    conn.send(
      nowEnvelope("ctrl.hello", {
        controller_version: "mock-footer-hints",
        ipc_protocol_version: IPC_PROTOCOL_VERSION,
      }),
    )
    conn.send(
      nowEnvelope("ctrl.state", {
        active_session_id: "mock-session",
        base_url: "http://127.0.0.1:9999",
        status: "running",
        current_model: "openai/gpt-5.2",
        pending_permissions: [],
      }),
    )

    void (async () => {
      await sleep(300)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: { type: "tool_result", payload: { result: { name: "apply_patch", ok: false } } },
          adapter_output: {
            stdout_text: "\n[tool] apply_patch (failed, expanded)\nerror in patch payload\n",
            summary_text: "apply_patch failed",
            normalized_event: {
              type: "tool.result",
              toolName: "apply_patch",
              ok: false,
            },
            hints: {
              lane: "tool",
              badge: "tool",
              tone: "danger",
              priority: "normal",
              stream: false,
            },
            tool_render: {
              mode: "expanded",
              reason: "tool-result-large",
            },
          },
        }),
      )

      await sleep(250)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: { type: "task_event", payload: { task_id: "t-1", status: "running", description: "index shard 01" } },
          adapter_output: {
            stdout_text: "\n[task] running · index shard 01\n",
            summary_text: "running · index shard 01",
            normalized_event: {
              type: "task.update",
              taskId: "t-1",
              taskStatus: "running",
              taskDescription: "index shard 01",
              taskLaneId: "lane-a",
              taskLaneLabel: "shard-worker-a",
              taskSubagentId: "sess-child-a",
              taskSubagentLabel: "Shard-A",
              parentSessionId: "sess-parent",
              childSessionId: "sess-child-a",
            },
            hints: {
              lane: "system",
              badge: "task",
              tone: "info",
              priority: "normal",
              stream: false,
            },
            overlay_intent: {
              kind: "task",
              action: "update",
              taskId: "t-1",
            },
          },
        }),
      )

      await sleep(250)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: { type: "task_event", payload: { task_id: "t-2", status: "running", description: "index shard 02" } },
          adapter_output: {
            stdout_text: "\n[task] running · index shard 02\n",
            summary_text: "running · index shard 02",
            normalized_event: {
              type: "task.update",
              taskId: "t-2",
              taskStatus: "running",
              taskDescription: "index shard 02",
              taskLaneId: "lane-b",
              taskLaneLabel: "shard-worker-b",
              taskSubagentId: "sess-child-b",
              taskSubagentLabel: "Shard-B",
              parentSessionId: "sess-parent",
              childSessionId: "sess-child-b",
            },
            hints: {
              lane: "system",
              badge: "task",
              tone: "info",
              priority: "normal",
              stream: false,
            },
            overlay_intent: {
              kind: "task",
              action: "update",
              taskId: "t-2",
            },
          },
        }),
      )

      await sleep(250)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: { type: "task_event", payload: { task_id: "t-1", status: "completed", description: "index shard 01" } },
          adapter_output: {
            stdout_text: "\n[task] completed · index shard 01\n",
            summary_text: "completed · index shard 01",
            normalized_event: {
              type: "task.update",
              taskId: "t-1",
              taskStatus: "completed",
              taskDescription: "index shard 01",
              taskLaneId: "lane-a",
              taskLaneLabel: "shard-worker-a",
              taskSubagentId: "sess-child-a",
              taskSubagentLabel: "Shard-A",
              parentSessionId: "sess-parent",
              childSessionId: "sess-child-a",
            },
            hints: {
              lane: "system",
              badge: "task",
              tone: "success",
              priority: "normal",
              stream: false,
            },
            overlay_intent: {
              kind: "task",
              action: "update",
              taskId: "t-1",
            },
          },
        }),
      )

      // Keep session alive briefly so tmux capture can assert footer hints.
      await sleep(3000)
      conn.send(nowEnvelope("ctrl.shutdown", { reason: "mock-finished" }))
      await server.close().catch(() => undefined)
      process.exit(0)
    })()
  })
}

await main()
