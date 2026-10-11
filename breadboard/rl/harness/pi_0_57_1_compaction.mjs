import { Agent, AssistantMessageEventStream, DEFAULT_COMPACTION_SETTINGS, PHASE_SCHEMA_VERSION, PROVIDER_API_KEY, buildSessionContext, calculateContextTokens, compact, convertMessages, convertToLlm, estimateContextTokens, fail, getLatestCompactionEntry, isContextOverflow, modelFromConfig, openaiCompletions, prepareCompaction, registerApiProvider, shouldCompact, unregisterApiProviders } from "./pi_0_57_1_runtime.mjs";
import { NativeDate, appendSessionEntry, nextTimestamp, prepareSession, replayState, sessionEntries, sessionManager, withReplayClock } from "./pi_0_57_1_replay.mjs";









function compactionReason(messages, settings, model) {
  // Trigger glue: 0.57.1 agent-session.js:1326-1393;
  // 0.73.1 agent-session.js:1376-1444. Overflow precedes threshold.
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

class CompactionPrepareSentinel extends Error {
  constructor() {
    super("bb-pi-0-57-1-compaction-prepare-sentinel");
  }
}

function modelForCompaction(state, payloadConfig) {
  if (payloadConfig) return modelFromConfig(payloadConfig);
  if (state?.model) return state.model;
  fail("prepare_compaction requires initialized worker or model_config");
}

async function prepareCompactionPhase(initializedState, payload) {
  if (!Array.isArray(payload?.messages)) fail("prepare_compaction requires messages");
  const wireModel = modelForCompaction(initializedState, payload.model_config);
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
      return { schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_unavailable", reason: "not_triggered" };
    }
  }
  if (reason === "overflow") {
    if (replayState.overflowRecoveryAttempted) {
      return { schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_unavailable", reason: "overflow_retry_exhausted" };
    }
    replayState.overflowRecoveryAttempted = true;
  }
  const preparation = prepareCompaction(pathEntries, settings);
  if (!preparation) {
    return { schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_unavailable", reason: "nothing_to_compact" };
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
      requests[callKinds[callIndex++]] = {
        messages: convertMessages(wireModel, context, wireModel.compat),
        max_tokens: options.maxTokens,
      };
      return { result: async () => { throw new CompactionPrepareSentinel(); } };
    },
  }, seamApi);
  const timestamp = nextTimestamp();
  try {
    // pi-ai stream.js:19-25 dispatches directly to the seam; no dummy key needed.
    // Stock summary clocks: 0.73.1 compaction.js:453,601; 0.57.1:444,598.
    await withReplayClock(timestamp, () => compact(preparation, { ...wireModel, api: seamApi }, undefined, payload.customInstructions, undefined));
  } catch (error) {
    if (!(error instanceof CompactionPrepareSentinel)) throw error;
  } finally { unregisterApiProviders(seamApi); }
  return {
    schema_version: PHASE_SCHEMA_VERSION,
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
  const wireModel = modelForCompaction(initializedState, payload.model_config);
  let result;
  try {
    result = await withReplayClock(nextTimestamp(), () => compact(preparation, { ...wireModel, api: seamApi }, undefined, prep.customInstructions, undefined));
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
    // agent-session.js:1481-1490; pi-agent-core agent.js:247-269 rejects a
    // retained assistant without steering/followups. Probe the actual method
    // at the provider seam; its private transcript is never persisted.
    const agent = new Agent({
      initialState: { model: wireModel, messages: structuredClone(messages) }, convertToLlm,
      streamFn: (model, context, options) => {
        retry = true;
        // Stock parser catches onPayload failures before network I/O (:239-249).
        return openaiCompletions.streamSimpleOpenAICompletions(model, context, {
          ...options, apiKey: PROVIDER_API_KEY, onPayload: () => { throw new CompactionPrepareSentinel(); },
        });
      },
    });
    await withReplayClock(replayState.replayClock, () => agent.continue().catch(() => {}));
  }
  return {
    schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_finalized", messages,
    summary: result.summary, reason: prep.reason, retry,
  };
}





export { finalizeCompactionPhase, prepareCompactionPhase };
