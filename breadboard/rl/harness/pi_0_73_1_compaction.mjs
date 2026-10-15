import { Agent, AssistantMessageEventStream, DEFAULT_COMPACTION_SETTINGS, buildSessionContext, calculateContextTokens, compact, convertMessages, convertToLlm, estimateContextTokens, fail, getLatestCompactionEntry, isContextOverflow, modelForProject, openaiCompletions, prepareCompaction, registerApiProvider, shouldCompact, unregisterApiProviders } from "./pi_0_73_1_runtime.mjs";
import { NativeDate, appendSessionEntry, nextTimestamp, prepareSession, replayState, sessionEntries, sessionManager, withReplayClock } from "./pi_0_73_1_replay.mjs";









class CompactionPrepareSentinel extends Error {
  constructor() {
    super("CompactionPrepareSentinel");
    this.name = "CompactionPrepareSentinel";
  }
}

function compactionReason(messages, settings, model) {
  // Trigger glue: 0.73.1 agent-session.js:1376-1444;
  // 0.57.1 agent-session.js:1326-1393. Overflow precedes threshold.
  const assistantMessage = messages.findLast((m) => m.role === "assistant");
  if (!settings.enabled || !assistantMessage || assistantMessage.stopReason === "aborted") return null;
  const compactionEntry = getLatestCompactionEntry(sessionEntries);
  const assistantIsFromBeforeCompaction = compactionEntry !== null
    && assistantMessage.timestamp <= new NativeDate(compactionEntry.timestamp).getTime();
  if (assistantIsFromBeforeCompaction) return null;
  const sameModel = assistantMessage.provider === model.provider && assistantMessage.model === model.id;
  if (sameModel && isContextOverflow(assistantMessage, model.contextWindow)) {
    return replayState.overflowRecoveryAttempted ? null : "overflow";
  }
  let contextTokens;
  if (assistantMessage.stopReason === "error") {
    const estimate = estimateContextTokens(messages);
    if (estimate.lastUsageIndex === null) return null;
    const usageMsg = messages[estimate.lastUsageIndex];
    if (compactionEntry && usageMsg.role === "assistant"
        && usageMsg.timestamp <= new NativeDate(compactionEntry.timestamp).getTime()) return null;
    contextTokens = estimate.tokens;
  } else {
    contextTokens = calculateContextTokens(assistantMessage.usage);
  }
  return shouldCompact(contextTokens, model.contextWindow, settings) ? "threshold" : null;
}

async function prepareCompactionPhase(initializedState, payload) {
  if (!Array.isArray(payload?.messages)) fail("prepare_compaction requires messages");
  const wireModel = modelForProject(initializedState.modelConfig);
  const pathEntries = await prepareSession(payload, wireModel);
  const settings = { ...DEFAULT_COMPACTION_SETTINGS, ...payload.settings };
  let reason = payload.reason;
  if (payload.reason === "threshold" || payload.checkpoint === "agent_end") {
    if (typeof payload.context_window !== "number" || payload.context_window <= 0) {
      fail("prepare_compaction requires context_window");
    }
    reason = compactionReason(buildSessionContext(pathEntries).messages, settings,
      { ...wireModel, contextWindow: payload.context_window });
    if (reason === null) {
      return { schema_version: "bb.pi-native.v1", kind: "compaction_unavailable", reason: "not_triggered" };
    }
  }
  if (reason === "overflow") {
    if (replayState.overflowRecoveryAttempted) {
      return { schema_version: "bb.pi-native.v1", kind: "compaction_unavailable", reason: "overflow_retry_exhausted" };
    }
    replayState.overflowRecoveryAttempted = true;
  }
  const preparation = prepareCompaction(pathEntries, settings);
  if (!preparation) {
    return { schema_version: "bb.pi-native.v1", kind: "compaction_unavailable", reason: "nothing_to_compact" };
  }
  const keptMessage = pathEntries.find((e) => e.id === preparation.firstKeptEntryId)?.message;
  const firstKeptIndex = keptMessage
    ? buildSessionContext(pathEntries).messages.indexOf(keptMessage) : -1;
  // Stock call order, not token-budget arithmetic:
  // 0.73.1 compaction.js:561-574; 0.57.1:558-571.
  const split = preparation.isSplitTurn && preparation.turnPrefixMessages.length > 0;
  const callKinds = split
    ? [...(preparation.messagesToSummarize.length ? ["history"] : []), "prefix"] : ["history"];
  const requests = {};
  let callIndex = 0;
  const seamApi = "bb-pi-compaction-capture";
  registerApiProvider({
    api: seamApi,
    stream: () => fail("unexpected stream call during compaction prepare"),
    streamSimple: (_model, context, options) => {
      const request = requests[callKinds[callIndex++]] = {
        messages: convertMessages(wireModel, context, wireModel.compat),
        max_tokens: options.maxTokens,
      };
      return { result: async () => {
        const stream = openaiCompletions.streamSimpleOpenAICompletions(wireModel, context, {
          ...options,
          apiKey: "EMPTY",
          onPayload: (wire) => {
            request.request_body = wire;
            request.request_body_json = JSON.stringify(wire);
            throw new CompactionPrepareSentinel();
          },
        });
        await stream.result();
        if (!request.request_body_json) fail("pinned Pi0.73 summary request capture failed");
        throw new CompactionPrepareSentinel();
      } };
    },
  }, seamApi);
  const timestamp = nextTimestamp();
  try {
    // pi-ai stream.js:19-25 dispatches directly to the seam; no dummy key needed.
    // Stock summary clocks: 0.73.1 compaction.js:453,601; 0.57.1:444,598.
    await withReplayClock(timestamp, () => compact(preparation, { ...wireModel, api: seamApi }, undefined, undefined, payload.customInstructions, undefined, undefined));
  } catch (error) {
    if (!(error instanceof CompactionPrepareSentinel)) throw error;
  } finally { unregisterApiProviders(seamApi); }
  return {
    schema_version: "bb.pi-native.v1",
    kind: "compaction_prepared",
    preparation: {
      ...preparation,
      firstKeptIndex,
      reason,
      customInstructions: payload.customInstructions,
      fileOps: {
        read: [...preparation.fileOps.read],
        written: [...preparation.fileOps.written],
        edited: [...preparation.fileOps.edited],
      },
    },
    summary_request: requests.history === undefined ? null : requests.history,
    turn_prefix_request: requests.prefix === undefined ? null : requests.prefix,
  };
}

async function finalizeCompactionPhase(initializedState, payload) {
  const prep = payload.preparation;
  if (!prep || typeof prep !== "object") fail("finalize_compaction requires preparation");
  const preparation = {
    ...prep,
    fileOps: {
      read: new Set(prep.fileOps.read),
      written: new Set(prep.fileOps.written),
      edited: new Set(prep.fileOps.edited),
    },
  };
  const split = prep.isSplitTurn && prep.turnPrefixMessages.length > 0;
  const answers = split
    ? [...(prep.messagesToSummarize.length ? [payload.summary] : []), payload.turn_prefix_summary]
    : [payload.summary];
  if (answers.some((text) => typeof text !== "string")) fail("each stock summary call requires its policy answer");
  let callIndex = 0;
  const seamApi = "bb-pi-compaction-replay";
  registerApiProvider({
    api: seamApi,
    stream: () => fail("unexpected stream call during compaction finalize"),
    streamSimple: () => {
      const text = answers[callIndex++];
      const stream = new AssistantMessageEventStream();
      stream.push({ type: "done", message: {
        role: "assistant", content: [{ type: "text", text }], stopReason: "stop",
      } });
      return stream;
    },
  }, seamApi);
  const wireModel = modelForProject(initializedState.modelConfig);
  let result;
  try {
    result = await withReplayClock(nextTimestamp(), () => compact(preparation, { ...wireModel, api: seamApi }, undefined, undefined, prep.customInstructions, undefined, undefined));
  } finally { unregisterApiProviders(seamApi); }
  appendSessionEntry({
    type: "compaction", summary: result.summary, firstKeptEntryId: result.firstKeptEntryId,
    tokensBefore: result.tokensBefore, details: result.details, fromHook: false,
  }, nextTimestamp());
  // Stock state rebuild and overflow-only error removal:
  // 0.73.1 agent-session.js:1543-1546,1564-1567; 0.57.1:1461-1464,1481-1485.
  let messages = sessionManager.buildSessionContext().messages;
  const last = messages[messages.length - 1];
  if (prep.reason === "overflow" && last?.role === "assistant" && last.stopReason === "error") {
    messages = messages.slice(0, -1);
  }
  replayState.observedMessages = structuredClone(messages);
  let retry = false;
  if (prep.reason === "overflow") {
    // Stock attempts Agent.continue().catch(() => {}) after rebuilding:
    // agent-session.js:1564-1571; Agent.continue rejects a retained assistant
    // without queued steering/followups (pi-agent-core agent.js:222-245).
    // Probe the actual public method at the provider seam, without mutating
    // SessionManager or returning the probe's private agent transcript.
    const agent = new Agent({
      initialState: { model: wireModel, messages: structuredClone(messages) }, convertToLlm,
      streamFn: (model, context, options) => {
        retry = true;
        // Stock parser catches onPayload failures before network I/O (:309-323).
        return openaiCompletions.streamSimpleOpenAICompletions(model, context, {
          ...options, apiKey: "EMPTY", onPayload: () => { throw new CompactionPrepareSentinel(); },
        });
      },
    });
    await withReplayClock(replayState.replayClock, () => agent.continue().catch(() => {}));
  }
  return {
    schema_version: "bb.pi-native.v1", kind: "compaction_finalized", messages,
    summary: result.summary, reason: prep.reason, retry,
  };
}





export { finalizeCompactionPhase, prepareCompactionPhase };
