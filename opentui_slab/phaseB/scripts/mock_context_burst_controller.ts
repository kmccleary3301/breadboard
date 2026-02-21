import { createIpcServer } from "../ipc.ts"
import { nowEnvelope, IPC_PROTOCOL_VERSION } from "../protocol.ts"

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const main = async () => {
  const server = await createIpcServer({ host: "127.0.0.1" })
  console.error(`IPC_HOST=${server.host} IPC_PORT=${server.port}`)

  server.onConnection((conn) => {
    conn.send(
      nowEnvelope("ctrl.hello", {
        controller_version: "mock-context-burst",
        ipc_protocol_version: IPC_PROTOCOL_VERSION,
      }),
    )
    conn.send(
      nowEnvelope("ctrl.state", {
        active_session_id: "mock-context-session",
        base_url: "http://127.0.0.1:9999",
        status: "running",
        current_model: "openai/gpt-5.2",
        pending_permissions: [],
      }),
    )

    void (async () => {
      await sleep(220)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: {
            type: "context_burst",
            count: 4,
            tool_counts: { read_file: 2, grep: 2 },
            failed_count: 0,
          },
          adapter_output: {
            stdout_text:
              "\n[context] 4 ops · read_file×2 · grep×2\n  - read_file (call)\n  - read_file (ok)\n  - grep (call)\n  - grep (ok)\n",
            summary_text: "[context] 4 ops · read_file×2 · grep×2",
            normalized_event: {
              type: "context.burst",
              count: 4,
              toolCounts: { read_file: 2, grep: 2 },
              failedCount: 0,
              summary: "[context] 4 ops · read_file×2 · grep×2",
              detail: "read_file call · read_file ok · grep call · grep ok",
            },
            context_block: {
              summary: "[context] 4 ops · read_file×2 · grep×2",
              detail: "read_file call · read_file ok · grep call · grep ok",
              block_text:
                "[context] 4 ops · read_file×2 · grep×2\n  - read_file (call)\n  - read_file (ok)\n  - grep (call)\n  - grep (ok)",
            },
            hints: {
              lane: "tool",
              badge: "context",
              tone: "info",
              priority: "normal",
              stream: false,
            },
            tool_render: {
              mode: "compact",
              reason: "context-burst-grouped",
            },
          },
        }),
      )

      await sleep(2500)
      conn.send(nowEnvelope("ctrl.shutdown", { reason: "mock-context-finished" }))
      await server.close().catch(() => undefined)
      process.exit(0)
    })()
  })
}

await main()
