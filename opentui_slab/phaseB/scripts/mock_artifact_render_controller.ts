import { createIpcServer } from "../ipc.ts"
import { nowEnvelope, IPC_PROTOCOL_VERSION } from "../protocol.ts"

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const main = async () => {
  const server = await createIpcServer({ host: "127.0.0.1" })
  console.error(`IPC_HOST=${server.host} IPC_PORT=${server.port}`)

  server.onConnection((conn) => {
    conn.send(
      nowEnvelope("ctrl.hello", {
        controller_version: "mock-artifact-render",
        ipc_protocol_version: IPC_PROTOCOL_VERSION,
      }),
    )
    conn.send(
      nowEnvelope("ctrl.state", {
        active_session_id: "mock-artifact-session",
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
            type: "tool_result",
            payload: {
              tool: "write_file",
              ok: true,
              display: {
                detail_artifact: {
                  schema_version: "artifact_ref_v1",
                  id: "artifact-sample-output",
                  kind: "tool_output",
                  mime: "text/plain",
                  path: "docs_tmp/tui_tool_artifacts/tool_output/artifact-sample-output.txt",
                  size_bytes: 399,
                  preview: {
                    lines: ["line-1: generated output preview", "line-2: generated output preview"],
                    note: "Inline output truncated to artifact reference.",
                  },
                },
              },
            },
          },
          adapter_output: {
            stdout_text:
              "\n[tool] write_file (ok, artifact) · tool_output · docs_tmp/tui_tool_artifacts/tool_output/artifact-sample-output.txt (399 bytes)\nnote: Inline output truncated to artifact reference.\n",
            summary_text:
              "artifact tool_output path=docs_tmp/tui_tool_artifacts/tool_output/artifact-sample-output.txt size=399",
            normalized_event: {
              type: "tool.result",
              toolName: "write_file",
              ok: true,
            },
            tool_render: {
              mode: "compact",
              reason: "artifact-ref",
            },
            hints: {
              lane: "tool",
              badge: "tool",
              tone: "success",
              priority: "normal",
              stream: false,
            },
          },
        }),
      )

      await sleep(180)
      conn.send(
        nowEnvelope("ctrl.event", {
          event: {
            type: "tool_result",
            payload: {
              tool: "apply_patch",
              ok: true,
              display: {
                detail_artifact: {
                  schema_version: "artifact_ref_v1",
                  id: "artifact-sample-diff",
                  kind: "tool_diff",
                  mime: "text/x-diff",
                  path: "docs_tmp/tui_tool_artifacts/tool_diff/artifact-sample-diff.diff",
                  size_bytes: 186,
                  preview: {
                    lines: ["diff --git a/src/main.c b/src/main.c", "@@ -1,3 +1,4 @@"],
                    note: "Large unified diff exported to artifact.",
                  },
                },
              },
            },
          },
          adapter_output: {
            stdout_text:
              "\n[tool] apply_patch (ok, artifact)\nartifact: tool_diff · docs_tmp/tui_tool_artifacts/tool_diff/artifact-sample-diff.diff (186 bytes)\npreview:\n  diff --git a/src/main.c b/src/main.c\n  @@ -1,3 +1,4 @@\nnote: Large unified diff exported to artifact.\n",
            summary_text:
              "artifact tool_diff path=docs_tmp/tui_tool_artifacts/tool_diff/artifact-sample-diff.diff size=186",
            normalized_event: {
              type: "tool.result",
              toolName: "apply_patch",
              ok: true,
            },
            tool_render: {
              mode: "expanded",
              reason: "artifact-diff",
            },
            hints: {
              lane: "tool",
              badge: "tool",
              tone: "success",
              priority: "normal",
              stream: false,
            },
          },
        }),
      )

      await sleep(220)
      conn.send(nowEnvelope("ctrl.shutdown", { reason: "mock-artifact-render-finished" }))
      await server.close().catch(() => undefined)
      process.exit(0)
    })()
  })
}

await main()
