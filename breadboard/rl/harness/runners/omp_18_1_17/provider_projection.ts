import { PHASE_SCHEMA, WORKER_API_KEY, parametersFor, sessionState } from "./tool_session.ts";
import { replayState, withJournalMetadata } from "./compaction_replay.ts";




function typeOfInvalidHttpFailure(payload: Record<string, unknown>): boolean {
  return typeof payload.http_status !== "number" || !Number.isInteger(payload.http_status) ||
    typeof payload.response_body_text !== "string" || !Array.isArray(payload.messages);
}

async function projectProviderFailure(payload: Record<string, any>): Promise<Record<string, any>> {
    if (!sessionState.session || typeOfInvalidHttpFailure(payload)) {
      throw new Error("project_provider_failure requires a session, HTTP status, body and messages");
    }
    const status = payload.http_status as number;
    const body = payload.response_body_text as string;
    const duration = payload.provider_request_duration_ms;
    if (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0) {
      throw new Error("project_provider_failure requires recorded provider request duration");
    }
    // Replay stock's own subtraction (openai-completions.ts:677,1496), not
    // a post-hoc message edit. The elapsed clock advances at transport completion.
    let elapsed = 0;
    return await withJournalMetadata(async () => {
      let message: Record<string, unknown> | undefined;
      for await (const event of sessionState.streamOpenAICompletions(
        sessionState.session.agent.state.model,
        { systemPrompt: sessionState.nativeSystemPrompt, messages: payload.messages as unknown[], tools: [] },
        { apiKey: WORKER_API_KEY, fetch: async () => {
          elapsed = duration;
          return new Response(body, { status });
        }, maxRetries: 0 },
      )) {
        if (event.type === "error") message = event.error;
      }
      if (!message) throw new Error("pinned provider did not emit an error assistant");
      return { schema_version: PHASE_SCHEMA, kind: "provider_failure", message };
    }, () => elapsed);
  }

async function parseProviderUsage(payload: Record<string, any>): Promise<Record<string, any>> {
    // The stock provider starts with its exported initial usage when no chunk
    // reports usage (openai-completions.ts:681; openai-shared.ts:3530-3547).
    return await withJournalMetadata(() => ({
      schema_version: PHASE_SCHEMA, kind: "assistant_usage",
      usage: payload.usage == null
        ? sessionState.createInitialAssistantMessage(sessionState.session.model.api, sessionState.session.model.provider, sessionState.session.model.id).usage
        : sessionState.parseChunkUsage(payload.usage as object, sessionState.session.model, undefined, 1000 + replayState.journalMessageId),
    }));
  }

async function projectRequest(payload: Record<string, any>): Promise<Record<string, any>> {
    if (sessionState.convertMessages === null) throw new Error("pinned OMP provider converter is unavailable");
    const model = sessionState.session.agent.state.model;
    if (!model) throw new Error("pinned OMP session model is unavailable");
    if (model.api !== "openai-completions") throw new Error(`pinned OMP provider converter requires an OpenAI Completions model: ${String(model.api)}`);
    if (sessionState.reminderInjector === null) throw new Error("pinned OMP date/cwd reminder is unavailable");
    const context = sessionState.reminderInjector.transform({
      systemPrompt: sessionState.nativeSystemPrompt,
      messages: sessionState.convertToLlm(payload.messages as unknown[]),
    }, sessionState.currentDate, sessionState.workspace);
    const messages = sessionState.convertMessages(model, context, model.compat);
    const tools = sessionState.tools.map((tool: { name: string }) => ({
      type: "function",
      function: { name: tool.name, description: sessionState.boundedDescriptions[tool.name], parameters: parametersFor(tool) },
    }));
    // The hash-verified supplier source root is selected at runtime.
    const { streamSimple } = await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/stream.ts`);
    let captured: Record<string, unknown> | null = null;
    const sentinel = new Error("OMP18_REQUEST_CAPTURE_SENTINEL");
    const stream = streamSimple(model, {
      ...context,
      tools: tools.map((tool) => tool.function),
    }, {
      apiKey: WORKER_API_KEY,
      onPayload: (wire: Record<string, unknown>) => {
        captured = wire;
        throw sentinel;
      },
    });
    for await (const _event of stream) {}
    if (!captured) throw new Error("pinned OMP18 primary request capture failed");
    const wireBody: Record<string, unknown> = captured;
    return {
      schema_version: PHASE_SCHEMA,
      kind: "request",
      messages,
      tools,
      request_members: Object.keys(wireBody).filter((member) => wireBody[member] !== undefined),
      request_body: captured,
      request_body_json: JSON.stringify(captured),
    };
  }



export { parseProviderUsage, projectProviderFailure, projectRequest };
