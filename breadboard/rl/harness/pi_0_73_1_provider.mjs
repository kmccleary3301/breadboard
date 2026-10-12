import { APIError, convertMessages, convertToLlm, fail, modelForProject, openaiCompletions, parseStreamingJson, safeJSON } from "./pi_0_73_1_runtime.mjs";
import { appendSessionEntry, nextTimestamp, replayState, syncSessionMessages, withReplayClock } from "./pi_0_73_1_replay.mjs";









async function projectRequest(initializedState, payload) {
  if (!initializedState) fail("project_request requires initialize");
  if (!Array.isArray(payload?.messages)) fail("project_request requires messages");
  const model = modelForProject(initializedState.modelConfig);
  const acceptsImage = model.input.includes("image");
  const messages = convertToLlm(payload.messages).map((message) => {
    if (acceptsImage || !Array.isArray(message?.content)) return message;
    return {
      ...message,
      content: message.content.filter((part) => part?.type !== "image"),
    };
  });
  const context = { systemPrompt: initializedState.systemPrompt, messages };
  const outboundMessages = convertMessages(model, context, model.compat);
  const tools = initializedState.schemas;
  let captured = null;
  const sentinel = new Error("Pi073_REQUEST_CAPTURE_SENTINEL");
  const stream = openaiCompletions.streamSimpleOpenAICompletions(model, {
    ...context,
    tools: tools.map((tool) => tool.function),
  }, {
    apiKey: "EMPTY",
    onPayload: (wire) => {
      captured = wire;
      throw sentinel;
    },
  });
  await stream.result();
  if (!captured) fail("pinned Pi0.73 primary request capture failed");
  return {
    schema_version: "bb.pi-native.v1",
    kind: "request",
    messages: outboundMessages,
    tools,
    request_members: Object.keys(captured).filter((member) => captured[member] !== undefined),
    request_body: captured,
    request_body_json: JSON.stringify(captured),
  };
}

async function projectProviderFailure(initializedState, payload) {
  if (!initializedState) fail("project_provider_failure requires initialize");
  if (!Number.isInteger(payload.http_status) || typeof payload.response_body_text !== "string") {
    fail("project_provider_failure requires HTTP status and response body");
  }
  await syncSessionMessages(payload.messages, modelForProject(initializedState.modelConfig));
  const errJSON = safeJSON(payload.response_body_text);
  // Stock SDK makeStatusError constructs this APIError; the stock pi-ai catch
  // builds the complete error AssistantMessage (openai-completions.js:309-323).
  const error = APIError.generate(payload.http_status, errJSON,
    errJSON ? undefined : payload.response_body_text, new Headers());
  const message = await withReplayClock(nextTimestamp(), async () => {
    const stream = openaiCompletions.streamSimpleOpenAICompletions(
      modelForProject(initializedState.modelConfig),
      { systemPrompt: initializedState.systemPrompt, messages: convertToLlm(payload.messages) },
      { apiKey: "EMPTY", onPayload: () => { throw error; } },
    );
    for await (const event of stream) { /* drain stock terminal error */ }
    return stream.result();
  });
  if (message.stopReason !== "error") fail("stock provider failure did not produce an error");
  appendSessionEntry({ type: "message", message }, message.timestamp);
  replayState.observedMessages.push(structuredClone(message));
  return { schema_version: "bb.pi-native.v1", kind: "provider_failure", message };
}

function parseStreamingJsonBatch(payload) {
    if (!Array.isArray(payload?.inputs)) fail("parse_streaming_json_batch requires inputs");
    const results = payload.inputs.map((input) => {
      if (input !== null && typeof input !== "string") fail("streaming JSON input must be a string or null");
      // Pinned parseStreamingJson owns every fallback, including {} for empty input.
      return parseStreamingJson(input ?? undefined);
    });
    return { schema_version: "bb.pi-native.v1", kind: "parsed_streaming_json_batch", results };

}





export { parseStreamingJsonBatch, projectProviderFailure, projectRequest };
