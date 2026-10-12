#!/opt/omp/runtime/bun-linux-x64-baseline/bun
import { close, executeBatch, initialize, prepareTools } from "./omp_18_1_17/tool_session.ts";
import { parseProviderUsage, projectProviderFailure, projectRequest } from "./omp_18_1_17/provider_projection.ts";
import { finalizeCompactionPhase, prepareCompactionPhase } from "./omp_18_1_17/compaction_replay.ts";
import { writeFrame } from "./omp_18_1_17/rpc_frames.ts";






const RPC_SCHEMA = "bb.native-worker.rpc.v1";

async function dispatch(operation: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (operation === "initialize") return initialize(payload as Record<string, any>);
  if (operation === "prepare_compaction") return prepareCompactionPhase(payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(payload);

  if (operation === "project_provider_failure") return projectProviderFailure(payload);
  if (operation === "parse_usage") return parseProviderUsage(payload);
  if (operation === "project_request") return projectRequest(payload);
  if (operation === "prepare_tools") return prepareTools(payload);
  if (operation === "execute_batch") return executeBatch(payload);
  if (operation === "close") return close(payload);
  throw new Error(`unknown OMP phase: ${operation}`);
}

const decoder = new TextDecoder();
const reader = Bun.stdin.stream().getReader();
let buffer = new Uint8Array(0);
function append(chunk: Uint8Array) { const next = new Uint8Array(buffer.length + chunk.length); next.set(buffer); next.set(chunk, buffer.length); buffer = next; }
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  append(value);
  while (buffer.length >= 4) {
    const length = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0, false);
    if (buffer.length < 4 + length) break;
    const payload = JSON.parse(decoder.decode(buffer.slice(4, 4 + length)));
    buffer = buffer.slice(4 + length);
    if (payload.schema_version !== RPC_SCHEMA || typeof payload.request_id !== "number") throw new Error("invalid worker frame");
    try {
      const result = await dispatch(payload.operation, payload.payload);
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, result });
    } catch (error) {
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, error: { type: "WorkerPhaseError", message: String(error) } });
    }
  }
}


