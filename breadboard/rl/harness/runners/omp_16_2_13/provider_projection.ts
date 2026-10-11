import { PHASE_SCHEMA, WORKER_API_KEY, pinnedSystemPrompt, sessionState } from "./tool_session.ts";



async function projectRequest(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!sessionState.session) throw new Error("worker must be initialized before project_request");

  if (!Array.isArray(payload.messages)) {
    throw new Error("project_request payload missing required array messages");
  }

  const model = sessionState.session.agent.state.model;
  // agent-loop.ts:1162 hands the session's string[] prompt to the provider as is.
  const systemPrompt = pinnedSystemPrompt();
  // agent-loop.ts:1141-1143 converts AgentMessage history before provider normalization.
  // compaction/messages.ts:190-206 turns compactionSummary into its summary user message.
  // Static imports cannot resolve the runtime-selected pinned package root.
  const { convertToLlm } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/session/messages.ts`
  );

  const context = {
    systemPrompt,
    messages: convertToLlm(payload.messages),
    tools: sessionState.normalizedTools,
  };

  let captured: any = null;
  const sentinel = new Error("OMP_REQUEST_CAPTURE_SENTINEL");

  try {
    const stream = sessionState.streamSimple(model, context, {
      apiKey: WORKER_API_KEY,
      onPayload: (params: any) => {
        captured = params;
        throw sentinel;
      },
    });
    for await (const _event of stream) {}
  } catch (err: unknown) {
    if (err !== sentinel && (err as { message?: string })?.message !== sentinel.message) {
      throw err;
    }
  }

  if (!captured) {
    throw new Error("project_request failed to capture params from streamSimple");
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "request",
    messages: captured.messages,
    tools: captured.tools,
    request_body: captured,
    request_members: Object.keys(captured),
  };
}


async function projectProviderFailure(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!sessionState.session) throw new Error("worker must be initialized before project_provider_failure");

  if (typeof payload.http_status !== "number") {
    throw new Error("project_provider_failure payload missing required number http_status");
  }
  if (typeof payload.response_body_text !== "string") {
    throw new Error("project_provider_failure payload missing required string response_body_text");
  }
  if (!Array.isArray(payload.messages)) {
    throw new Error("project_provider_failure payload missing required array messages");
  }

  const status = payload.http_status;
  const errorBody = payload.response_body_text;
  const messages = payload.messages;

  const model = sessionState.session.agent.state.model;
  const context = {
    systemPrompt: sessionState.session.agent.state.systemPrompt,
    messages,
    tools: [],
  };

  // The Conductor carries only the upstream status and body; the pinned SDK
  // builds its error from those two (no status text or header is invented).
  const mockFetch = async () => new Response(errorBody, { status });

  const stream = sessionState.streamSimple(model, context, {
    apiKey: WORKER_API_KEY,
    fetch: mockFetch,
  });

  let errorEvent: any = null;
  for await (const event of stream) {
    if (event.type === "error") {
      errorEvent = event;
    }
  }

  if (!errorEvent || !errorEvent.error) {
    throw new Error("pinned streamSimple did not emit an error event for provider failure");
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "provider_failure",
    message: errorEvent.error,
  };
}

function parseStreamingJsonBatch(payload: Record<string, any>): Record<string, any> {
  if (!sessionState.parseStreamingJson) {
    throw new Error("pinned parseStreamingJson is unavailable");
  }
  if (!Array.isArray(payload.inputs)) {
    throw new Error("parse_streaming_json_batch payload missing required array inputs");
  }
  const inputs = payload.inputs as Array<string | null>;
  const results = inputs.map((input) => sessionState.parseStreamingJson!(input === null ? undefined : input));
  return {
    schema_version: PHASE_SCHEMA,
    kind: "parsed_streaming_json_batch",
    results,
  };
}


async function parseProviderUsage(payload: { usage: object | null }) {
  if (!sessionState.session) throw new Error("worker must be initialized before parse_provider_usage");
  // Runtime-selected pinned modules. Stock stream parser: openai-completions.ts:1011-1013,1533-1578.
  const { parseChunkUsage } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  const { createInitialResponsesAssistantMessage } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-shared.ts`
  );
  const model = sessionState.session.model;
  // No-usage stream: openai-completions.ts:565. The factory's timestamp is
  // discarded; only its stock initial usage crosses this deterministic seam.
  const usage = payload.usage === null
    ? createInitialResponsesAssistantMessage(model.api, model.provider, model.id).usage
    : parseChunkUsage(payload.usage, model, undefined);
  return { schema_version: PHASE_SCHEMA, kind: "assistant_usage", usage };
}




export { parseProviderUsage, parseStreamingJsonBatch, projectProviderFailure, projectRequest };
