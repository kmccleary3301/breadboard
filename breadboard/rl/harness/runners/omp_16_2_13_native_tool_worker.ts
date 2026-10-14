#!/usr/bin/env bun
import { close, executeBatch, initialize, prepareTools } from "./omp_16_2_13/tool_session.ts";
import { parseProviderUsage, parseStreamingJsonBatch, projectProviderFailure, projectRequest } from "./omp_16_2_13/provider_projection.ts";
import { finalizeCompactionPhase, prepareCompactionPhase } from "./omp_16_2_13/compaction_replay.ts";






const RPC_SCHEMA = "bb.native-worker.rpc.v1";
type RpcPayload = Record<string, unknown>;
type RpcResult = Record<string, unknown>;
type RpcFrame = { schema_version: string; request_id: number; operation: string; payload: RpcPayload };

async function dispatch(operation: string, payload: RpcPayload): Promise<RpcResult> {
  switch (operation) {
    case "initialize":
      return initialize(payload);
    case "project_request":
      return projectRequest(payload);
    case "prepare_tools":
      return prepareTools(payload);
    case "execute_batch":
      return executeBatch(payload);
    case "project_provider_failure":
      return projectProviderFailure(payload);
    case "parse_streaming_json_batch":
      return parseStreamingJsonBatch(payload);
    case "parse_provider_usage":
      return parseProviderUsage(payload as Parameters<typeof parseProviderUsage>[0]);
    case "prepare_compaction":
      return prepareCompactionPhase(payload);
    case "finalize_compaction":
      return finalizeCompactionPhase(payload);
    case "close":
      return close();
    default:
      throw new Error(`unknown OMP 16.2.13 phase: ${operation}`);
  }
}

// Framed Bun stdin/stdout loop with 4-byte BE uint32 length prefix
const decoder = new TextDecoder();
const reader = Bun.stdin.stream().getReader();
let buffer = new Uint8Array(0);

function append(chunk: Uint8Array) {
  const next = new Uint8Array(buffer.length + chunk.length);
  next.set(buffer);
  next.set(chunk, buffer.length);
  buffer = next;
}

async function writeFrame(value: RpcResult) {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  const frame = new Uint8Array(4 + encoded.length);
  new DataView(frame.buffer).setUint32(0, encoded.length, false);
  frame.set(encoded, 4);
  await new Promise<void>((resolve, reject) => {
    process.stdout.write(frame, (error) => error ? reject(error) : resolve());
  });
}

while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  append(value);
  while (buffer.length >= 4) {
    const length = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0, false);
    if (buffer.length < 4 + length) break;
    const payload = JSON.parse(decoder.decode(buffer.slice(4, 4 + length))) as RpcFrame;
    buffer = buffer.slice(4 + length);
    if (payload.schema_version !== RPC_SCHEMA || typeof payload.request_id !== "number") {
      throw new Error("invalid worker frame");
    }
    try {
      const result = await dispatch(payload.operation, payload.payload);
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, result });
    } catch (error: unknown) {
      await writeFrame({
        schema_version: RPC_SCHEMA,
        request_id: payload.request_id,
        error: { type: "WorkerPhaseError", message: error instanceof Error ? error.message : String(error) },
      });
    }
  }
}



