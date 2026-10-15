import { randomBytes } from "node:crypto";
import { APIError, PHASE_SCHEMA_VERSION, PROVIDER_API_KEY, convertToLlm, fail, isPlainObject, parseStreamingJson, requireExactKeys, safeJSON, streamSimpleOpenAICompletions } from "./pi_0_57_1_runtime.mjs";
import { appendSessionEntry, nextTimestamp, replayState, syncSessionMessages, withReplayClock } from "./pi_0_57_1_replay.mjs";












/**
 * Pinned LLM context for one agent-loop turn: agent-loop.js:137-146 runs
 * transformContext (no extension runner -> identity, sdk.js:176-181) and then
 * convertToLlm (core/messages.js:75-122 via sdk.js:130-135).
 */
function llmContext(initializedState, messages) {
  if (!Array.isArray(messages)) fail("messages must be a list");
  for (const message of messages) {
    if (!isPlainObject(message) || !["user", "assistant", "toolResult", "compactionSummary"].includes(message.role)) {
      fail("messages must be pinned AgentMessages");
    }
  }
  return {
    systemPrompt: initializedState.systemPrompt,
    messages: convertToLlm(messages),
    tools: initializedState.tools,
  };
}

/**
 * Pinned stream options for `--thinking off`: agent.js:286 sets reasoning
 * undefined for thinking level "off" and agent-loop.js:151-155 passes
 * {...config, apiKey, signal}.  Of those config fields openai-completions only
 * reads apiKey, signal, onPayload and reasoning (openai-completions.js:254-266,
 * simple-options.js:1-14); maxTokens is not passed, so pinned buildBaseOptions
 * derives Math.min(model.maxTokens, 32000) (simple-options.js:4).
 */
async function runPinnedStream(initializedState, messages, onPayload, signal) {
  const stream = streamSimpleOpenAICompletions(initializedState.model, llmContext(initializedState, messages), {
    reasoning: undefined,
    apiKey: PROVIDER_API_KEY,
    signal,
    onPayload,
  });
  for await (const _event of stream) {
    // Drain the pinned event stream; its terminal message is the result.
  }
  return stream.result();
}

async function projectRequest(initializedState, payload) {
  if (!initializedState) fail("project_request requires initialize");
  requireExactKeys(payload, ["messages"], "project_request payload");
  const sentinel = new Error(`bb-pi-0.57.1-request-projection-${randomBytes(16).toString("hex")}`);
  let captured = null;
  // openai-completions.js:51-56 builds params and awaits onPayload before
  // client.chat.completions.create, so throwing here performs no I/O and the
  // pinned catch (:239-249) turns it into the terminal error message.
  const message = await runPinnedStream(initializedState, payload.messages, (params) => {
    captured = params;
    throw sentinel;
  }, new AbortController().signal);
  if (captured === null || message.stopReason !== "error" || message.errorMessage !== sentinel.message) {
    fail("pinned request projection did not stop at the payload sentinel");
  }
  // openai-completions.js:604-615 (convertTools) appends `strict: false` to
  // each function under compat.supportsStrictMode; that member is a request
  // builder concern the caller profile re-emits (strict_tools), so the source
  // tool surface carries the pinned schemas without it and request_body keeps
  // the exact pinned body for the Conductor's equality check.
  const tools = captured.tools.map((tool, index) => {
    const { strict, ...fn } = tool.function;
    const reference = initializedState.tools[index];
    if (
      strict !== false || Object.keys(tool).length !== 2 || reference === undefined
      || fn.name !== reference.name || fn.description !== reference.description || fn.parameters !== reference.parameters
    ) {
      fail("pinned request tools differ from the initialized tool registry");
    }
    return { type: tool.type, function: fn };
  });
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "request",
    messages: captured.messages,
    tools,
    request_members: Object.keys(captured).filter((member) => captured[member] !== undefined),
    request_body: captured,
    request_body_json: JSON.stringify(captured),
  };
}

async function projectProviderFailure(initializedState, payload) {
  if (!initializedState) fail("project_provider_failure requires initialize");
  requireExactKeys(payload, ["http_status", "response_body_text", "messages"], "project_provider_failure payload");
  const status = payload.http_status;
  const errText = payload.response_body_text;
  if (!Number.isInteger(status) || status < 100 || status > 599) fail("http_status must be an HTTP status code");
  if (typeof errText !== "string") fail("response_body_text must be a string");
  await syncSessionMessages(payload.messages, initializedState.model);
  // openai@6.26.0 client.mjs:351-353,362 (makeRequest) and :193-195
  // (makeStatusError): safeJSON(errText), message only when the body is not
  // JSON, then APIError.generate(status, errJSON, errMessage, response.headers).
  const errJSON = safeJSON(errText);
  const errMessage = errJSON ? undefined : errText;
  const error = APIError.generate(status, errJSON, errMessage, new Headers());
  // The SDK error surfaces from client.chat.completions.create
  // (openai-completions.js:56); throwing it from onPayload (:52) reaches the
  // same pinned catch (:239-249), which builds the terminal assistant message.
  const message = await withReplayClock(nextTimestamp(), () => runPinnedStream(initializedState, payload.messages, () => {
    throw error;
  }, new AbortController().signal));
  if (message.stopReason !== "error") fail("pinned provider failure did not produce an error message");
  appendSessionEntry({ type: "message", message }, message.timestamp);
  replayState.observedMessages.push(structuredClone(message));
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "provider_failure", message };
}

function parseStreamingJsonBatch(payload) {
  requireExactKeys(payload, ["inputs"], "parse_streaming_json_batch payload");
  if (!Array.isArray(payload.inputs)) fail("parse_streaming_json_batch requires inputs");
  const results = payload.inputs.map((input) => {
    if (input !== null && typeof input !== "string") fail("streaming JSON input must be a string or null");
    // Pinned parseStreamingJson owns every fallback, including {} for empty input.
    return parseStreamingJson(input ?? undefined);
  });
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "parsed_streaming_json_batch", results };
}





export { parseStreamingJsonBatch, projectProviderFailure, projectRequest };
