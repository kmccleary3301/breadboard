import { isDeepStrictEqual } from "node:util";

export function createCompactionController({ supplier, context, protocol, episodeSourceTools, toSourceHistory, verifyAndLoad, providerExchange }) {
let sourceOverflowRecoveryAttempts = 0;
let sourceLastAdmissionTimestamp = null;
let sourceCompactionSnapshot = null;
let sourceCompactionContinuation = null;
function estimateFreshLlmBoundaryTokenPressure(params) {
  const toolTokens = params.tools?.length ? supplier.estimateJsonPayloadTokenPressure(params.tools.map(({ name, description, parameters }) => ({
    name,
    description,
    parameters
  }))) : 0;
  return Math.ceil((supplier.estimateRenderedPromptTokens(params) + toolTokens + (params.imageCount ?? 0) * supplier.IMAGE_BLOCK_TOKENS + params.messages.reduce((total, message) => total + supplier.estimateMessageTokenPressure(message), 0)) * supplier.SAFETY_MARGIN);
}

function estimateCompactionHistoryTokens(messages, budget) {
  const pending = budget?.pendingTokens && budget.pendingUserIdempotencyKey ? messages.findLast((message) => message.role === "user" && "idempotencyKey" in message && message.idempotencyKey === budget.pendingUserIdempotencyKey) : void 0;
  const overlap = pending ? Math.min(estimateCompactionHistoryTokens([pending]), budget?.pendingUserTokens ?? budget?.pendingTokens ?? 0) : 0;
  return estimateFreshLlmBoundaryTokenPressure({
    messages,
    prompt: ""
  }) - estimateFreshLlmBoundaryTokenPressure({
    messages: [],
    prompt: ""
  }) - overlap;
}

function resolveCompactionRetentionBudget(budget, messages) {
  const preferredTokens = budget.contextWindow - budget.reserveTokens - budget.fixedTokens - budget.pendingTokens;
  return {
    maxTokens: preferredTokens <= 0 ? estimateCompactionHistoryTokens(messages, budget) - 1 : preferredTokens,
    reserveTokens: estimateCompactionHistoryTokens([{
      role: "compactionSummary",
      summary: "x".repeat(supplier.MAX_COMPACTION_SUMMARY_CHARS),
      tokensBefore: 0,
      timestamp: 0
    }])
  };
}

function createReplaySessionManager(entries, timestamp) {
  // Selected model path: session-manager-DZHCo5g0.mjs:1477-1483;
  // stock header:380-390. Clone because this factory consumes owned entries.
  const manager = supplier.sourceSessionManager.fromSelectedEntries(structuredClone([
    { type: "session", version: 4,
      id: providerExchange.exchanges.has("embeddings") ? context.sourceSessionIdentity : "compaction-replay",
      timestamp, cwd: context.workspace },
    ...entries,
  ]), context.workspace);
  const appendEntry = manager.appendEntry;
  let nextId = entries.length;
  // Only the id/clock seam. Real appendMessage/appendCompaction execute:
  // session-manager-DZHCo5g0.mjs:995-998,1034-1036.
  manager.appendEntry = function(entry, options) {
    entry.id = `replay_${nextId++}`;
    entry.timestamp = timestamp;
    return appendEntry.call(this, entry, options);
  };
  return manager;
}

function createCompactionReplay(preparation, streamFn) {
  const manager = createReplaySessionManager(preparation.entries, preparation.timestamp);
  const agent = new supplier.sourceAgentCore.t({
    initialState: {
      model: context.modelConfig, systemPrompt: preparation.systemPrompt,
      // Agent state may omit a length/error still saved in the ledger:
      // resource-loader-Bu_pVD2t.mjs:10107-10109,10183-10186.
      messages: preparation.hasCommittedCheckpoint ? preparation.messages : manager.buildSessionContext().messages,
      tools: episodeSourceTools(),
    },
    streamFn,
  });
  // Invoke the exported session methods without starting the interactive host.
  const session = Object.create(supplier.sourceResource.n.prototype);
  session.agent = agent;
  session.sessionManager = manager;
  session.settingsManager = supplier.sourceResource.rt.inMemory({ compaction: preparation.settings });
  session.overflowRecoveryAttempts = preparation.overflowRecoveryAttempts;
  // Stock headless ownership: resource-loader-Bu_pVD2t.mjs:8330.
  session.contextOverflowRecoveryOwner = "session";
  session.currentExtensionRunner = new supplier.sourceResource.b([], undefined, context.workspace, manager, undefined);
  // The replay transport supplies the response; it needs no provider credentials.
  // Stock custom-stream auth permits this (resource-loader-Bu_pVD2t.mjs:8350-8356).
  session.getCompactionRequestAuth = async () => ({});
  return session;
}

function replaySummaryResponse(summary) {
  const response = { stopReason: "stop", content: [{ type: "text", text: summary }] };
  return {
    async *[Symbol.asyncIterator]() {},
    result: async () => response,
  };
}

async function runAdmittedOverflowCompaction(session, requestBudget) {
  // The conductor already classified a refused provider request as overflow.
  // Its private recovery branch is verbatim, with this -> session only:
  // resource-loader-Bu_pVD2t.mjs:10094-10109. Keep the same counter for length
  // and HTTP overflows; do not manufacture an assistant error to call the gate.
  if (session.contextOverflowRecoveryOwner === "caller") return false;
  if (session.overflowRecoveryAttempts >= 3) {
    session.emit({
      type: "compaction_end",
      reason: "overflow",
      outcome: {
        status: "failed",
        reason: `Context overflow recovery failed after 3 compact-and-retry attempts. Try reducing context or switching to a larger-context model.`
      }
    });
    return false;
  }
  session.overflowRecoveryAttempts += 1;
  const messages = session.agent.state.messages;
  if (messages.at(-1)?.role === "assistant") session.agent.state.messages = messages.slice(0, -1);
  return await session.runAutoCompaction("overflow", true, requestBudget);
}

function runCompactionReplay(preparation, streamFn) {
  const session = createCompactionReplay(preparation, streamFn);
  let workOutcome;
  let automaticOutcome;
  session.emit = (event) => {
    if (event.type === "compaction_start") preparation.sourceReason = event.reason;
    if (event.type === "compaction_end") automaticOutcome = event.outcome;
  };
  const stockWork = session.runCompactionWork.bind(session);
  // Deterministic item id only: stock allocates it at resource-loader:10131.
  session.runCompactionWork = (options) => stockWork({ ...options, itemId: "compaction_replay" })
    .then((outcome) => { workOutcome = outcome; return outcome; });
  // The exported automatic caller owns failure framing (10190-10212), not RPC.
  const run = preparation.reason === "threshold"
    ? session.checkCompaction(preparation.triggerMessage, true, preparation.requestBudget)
    : runAdmittedOverflowCompaction(session, preparation.requestBudget);
  return run.then((retry) => ({
    outcome: automaticOutcome?.status === "completed" ? workOutcome
      : automaticOutcome || { status: "skipped", reason: "not_triggered" },
    session, retry, reason: preparation.sourceReason,
  }));
}

function admitSourceRecoveryEvents(messages) {
  for (const msg of messages) {
    if ((msg.role !== "assistant" && msg.role !== "user") || typeof msg.timestamp !== "number") continue;
    if (sourceLastAdmissionTimestamp !== null && msg.timestamp <= sourceLastAdmissionTimestamp) continue;
    // Agent-exec's caller-owned counter survives assistant/tool progress:
    // embedded-agent-CE9KzQvy.mjs:3093-3095. Only a new canonical user turn
    // begins another run; the transient continuation is never admitted here.
    if (msg.role === "user") sourceOverflowRecoveryAttempts = 0;
    sourceLastAdmissionTimestamp = msg.timestamp;
  }
}
async function prepareCompaction(message) {
    if (!context.modelConfig || typeof context.modelConfig !== "object") {
      throw new Error("worker is not initialized with model_config");
    }
    if (!supplier.sourceCompaction) await verifyAndLoad();
    const reason = message.reason;
    const systemMessage = message.messages[0]?.role === "system" ? message.messages[0] : null;
    const admittedMessages = systemMessage ? message.messages.slice(1) : message.messages;
    const rawMessages = toSourceHistory(admittedMessages);
    const latestAssistantIndex = admittedMessages.findLastIndex((msg) => msg.role === "assistant");
    if (message.usage && latestAssistantIndex >= 0
      && !Object.hasOwn(admittedMessages[latestAssistantIndex], "usage")
      && !admittedMessages[latestAssistantIndex].providerUsage) {
      rawMessages[latestAssistantIndex] = { ...rawMessages[latestAssistantIndex], usage: message.usage };
    }
    admitSourceRecoveryEvents(rawMessages);
    const settings = message.settings;
    const contextWindow = context.modelConfig.contextWindow;
    const settingsManager = supplier.sourceResource.rt.inMemory({ compaction: settings });
    // Stock defaults and overrides, not worker-invented fallbacks:
    // resource-loader-Bu_pVD2t.mjs:2482-2499; agent-settings-DcI_VuTd.mjs:12-37.
    supplier.applyAgentCompactionSettingsFromConfig({
      settingsManager, cfg: context.sourceConfig, contextTokenBudget: contextWindow,
    });
    const effectiveSettings = settingsManager.getCompactionSettings();


    // Persisted replay clocks/ids replace stock's clock and random entry ids only:
    // session-manager-DZHCo5g0.mjs:995-998,1034-1036.
    const latestTimestamp = rawMessages.reduce(
      (latest, msg) => typeof msg.timestamp === "number" ? Math.max(latest, msg.timestamp) : latest, context.sourceInitialTimestamp,
    );
    const timestamp = new Date(latestTimestamp + 1).toISOString();
    const entries = rawMessages.map((msg, index) => {
      // Replace persisted random ids with replay indexes; stock appends ids at
      // session-manager-DZHCo5g0.mjs:1034,995.
      if (msg && typeof msg === "object" && msg.role === "compactionSummary" && typeof msg.summary === "string") {
        return {
          id: `entry_${index}`,
          parentId: index === 0 ? null : `entry_${index - 1}`,
          type: "compaction",
          summary: msg.summary,
          firstKeptEntryId: `entry_${index + 1}`,
          tokensBefore: msg.tokensBefore,
          timestamp: typeof msg.timestamp === "number" ? new Date(msg.timestamp).toISOString() : msg.timestamp,
        };
      }
      return {
        id: `entry_${index}`,
        parentId: index === 0 ? null : `entry_${index - 1}`,
        timestamp,
        type: "message",
        message: msg,
      };
    });
    // Keep the actual committed ledger: stock uses previous compaction details
    // (compaction-DhVoBTx3.mjs:252-258,674-676,751-753), absent from AgentMessages.
    if (sourceCompactionSnapshot) {
      if (rawMessages.length < sourceCompactionSnapshot.messages.length
        || !isDeepStrictEqual(rawMessages.slice(0, sourceCompactionSnapshot.messages.length), sourceCompactionSnapshot.messages)) {
        throw new Error("compaction history differs from the committed source checkpoint");
      }
      const manager = createReplaySessionManager(sourceCompactionSnapshot.entries, timestamp);
      for (const msg of rawMessages.slice(sourceCompactionSnapshot.messages.length)) manager.appendMessage(msg);
      entries.splice(0, entries.length, ...manager.getBranch());
    }

    const systemPrompt = systemMessage ? systemMessage.content : "";
    const requestBudget = supplier.sourceResource.f({
      contextWindow, reserveTokens: effectiveSettings.reserveTokens,
      systemPrompt, tools: episodeSourceTools(),
    });
    const preparation = {
      settings: effectiveSettings, reason, entries, systemPrompt, systemMessage,
      messages: rawMessages,
      hasCommittedCheckpoint: sourceCompactionSnapshot !== null,
      requestBudget, timestamp, triggerMessage: rawMessages[latestAssistantIndex],
      overflowRecoveryAttempts: sourceOverflowRecoveryAttempts,
    };
    const capturedRequests = [];
    const firstCaptured = Promise.withResolvers();
    const releaseHistory = Promise.withResolvers();
    const prefixCaptured = Promise.withResolvers();
    const replay = runCompactionReplay(preparation, (m, context, options) => {
      const wire = supplier.sourceTransport.t(m, context, options);
      capturedRequests.push({ messages: wire.messages, max_tokens: wire.max_completion_tokens ?? wire.max_tokens });
      if (capturedRequests.length === 1) {
        firstCaptured.resolve();
        return releaseHistory.promise;
      }
      prefixCaptured.resolve();
      return new Promise(() => {});
    });
    // Stock checkCompaction gates first; runAutoCompaction owns all planner
    // failures (resource-loader-Bu_pVD2t.mjs:10085-10122,10190-10212).
    const admission = await Promise.race([replay, firstCaptured.promise]);
    if (admission !== undefined) {
      sourceOverflowRecoveryAttempts = admission.session.overflowRecoveryAttempts;
      return { schema_version: protocol, kind: "compaction_unavailable", reason: admission.outcome.reason };
    }
    // The real stock planner has now succeeded and reached its provider await.
    // Re-run its exported planner only to serialize protocol preparation metadata.
    const retention = resolveCompactionRetentionBudget(requestBudget, supplier.sourceSession.t(entries).messages);
    const prepResult = supplier.sourceCompaction.g(entries, effectiveSettings, preparation.sourceReason === "overflow" ? "unresolved" : undefined, {
      budget: { ...retention, estimateTokens: (msg) => estimateCompactionHistoryTokens([msg], requestBudget) },
    });
    if (!prepResult.ok) throw prepResult.error;
    const prep = prepResult.value;
    preparation.firstKeptEntryId = prep.firstKeptEntryId;
    preparation.isSplitTurn = prep.isSplitTurn;
    preparation.tokensBefore = prep.tokensBefore;
    // Budget sizing is stock's private work glue, with only the replay timestamp/id:
    // resource-loader-Bu_pVD2t.mjs:9995-10005.
    const projectReplacement = (result, summary) => supplier.sourceSession.t([...entries, {
      ...result, type: "compaction", id: "compaction_replay",
      parentId: entries.at(-1)?.id ?? null, timestamp, summary,
    }]).messages;
    const requestTokenLimit = requestBudget.fixedTokens + requestBudget.pendingTokens + retention.maxTokens;
    const remaining = requestTokenLimit - supplier.sourceResource.p(projectReplacement(prep, ""), requestBudget);
    prep.summaryTokenBudget = Math.floor(remaining / supplier.SAFETY_MARGIN) - 1;
    preparation.summaryTokenBudget = prep.summaryTokenBudget;
    const summarizeTurnPrefix = prep.isSplitTurn && prep.turnPrefixMessages.length > 0;
    const hasHistory = prep.messagesToSummarize.length > 0 || !summarizeTurnPrefix;
    if (hasHistory && summarizeTurnPrefix) {
      // compact() awaits history before prefix (compaction-DhVoBTx3.mjs:753-760).
      // This provider-only dummy exposes the prefix await; no fitting or commit
      // executes in the prepare-only session, even for a tiny summary budget.
      releaseHistory.resolve(replaySummaryResponse("DUMMY_SUMMARY"));
      const next = await Promise.race([replay, prefixCaptured.promise]);
      if (next !== undefined) {
        return { schema_version: protocol, kind: "compaction_unavailable", reason: next.outcome.reason };
      }
    }
    const firstKeptMessage = entries.find((entry) => entry.id === prep.firstKeptEntryId).message;
    preparation.firstKeptIndex = supplier.sourceSession.t(entries).messages.indexOf(firstKeptMessage) + (systemMessage ? 1 : 0);
    const summaryRequest = hasHistory ? capturedRequests[0] : null;
    const turnPrefixRequest = summarizeTurnPrefix ? capturedRequests[hasHistory ? 1 : 0] : null;
    preparation.summaryRequest = summaryRequest;
    preparation.turnPrefixRequest = turnPrefixRequest;

    preparation.prep = { ...prep, fileOps: {
      read: [...prep.fileOps.read], written: [...prep.fileOps.written], edited: [...prep.fileOps.edited],
    } };

    return {
      schema_version: protocol,
      kind: "compaction_prepared",
      preparation,
      summary_request: summaryRequest,
      turn_prefix_request: turnPrefixRequest,
    };
  }
async function finalizeCompaction(message) {
    if (!context.modelConfig || typeof context.modelConfig !== "object") {
      throw new Error("worker is not initialized with model_config");
    }
    if (!supplier.sourceCompaction) await verifyAndLoad();
    const prep = message.preparation;
    if (!prep || typeof prep !== "object") throw new Error("finalize_compaction requires preparation");
    const summarizeTurnPrefix = prep.prep.isSplitTurn && prep.prep.turnPrefixMessages.length > 0;
    const hasHistory = prep.prep.messagesToSummarize.length > 0 || !summarizeTurnPrefix;

    const answers = [];
    if (hasHistory && typeof message.summary === "string") {
      answers.push({ request: prep.summaryRequest, summary: message.summary });
    }
    if (summarizeTurnPrefix) {
      const prefix = hasHistory ? message.turn_prefix_summary : message.turn_prefix_summary ?? message.summary;
      if (typeof prefix === "string") answers.push({ request: prep.turnPrefixRequest, summary: prefix });
    }
    const followupAnswers = Array.isArray(message.followup_summaries) ? message.followup_summaries : [];

    // Suspend at the stock provider await only; stock decides whether invalid
    // output retries once (resource-loader-Bu_pVD2t.mjs:10035-10040).
    const followup = Promise.withResolvers();
    let followupIdx = 0;
    const streamFn = (m, context, options) => {
      const wireParams = supplier.sourceTransport.t(m, context, options);
      const request = { messages: wireParams.messages, max_tokens: wireParams.max_completion_tokens ?? wireParams.max_tokens };
      const initialIdx = answers.findIndex((answer) => isDeepStrictEqual(answer.request, request));
      if (initialIdx >= 0) return replaySummaryResponse(answers.splice(initialIdx, 1)[0].summary);
      if (followupIdx < followupAnswers.length) return replaySummaryResponse(followupAnswers[followupIdx++]);
      followup.resolve({ schema_version: protocol, kind: "compaction_followup_request", request });
      return new Promise(() => {});
    };

    const completed = await Promise.race([runCompactionReplay(prep, streamFn), followup.promise]);
    if (completed.kind === "compaction_followup_request") return completed;
    sourceOverflowRecoveryAttempts = completed.session.overflowRecoveryAttempts;
    if (completed.outcome.status !== "completed") {
      return { schema_version: protocol, kind: "compaction_unavailable", reason: completed.outcome.reason };
    }
    const finalSummary = completed.outcome.result.summary;
    const compaction_message = completed.session.agent.state.messages.find((msg) => msg.role === "compactionSummary");
    const messages = [
      ...(prep.systemMessage ? [prep.systemMessage] : []),
      ...completed.session.agent.state.messages,
    ];
    sourceCompactionSnapshot = {
      entries: completed.session.sessionManager.getBranch(),
      messages: structuredClone(completed.session.agent.state.messages),
    };
    sourceCompactionContinuation = completed.retry ? {
      index: sourceCompactionSnapshot.messages.length,
      message: {
        role: "user", content: supplier.sourceContinuationPrompt,
        timestamp: new Date(prep.timestamp).getTime(),
      },
    } : null;
    if (providerExchange.exchanges.has("embeddings") && completed.reason === "overflow") {
      await providerExchange.startSourceIndexing(completed.session.sessionManager);
    } else if (providerExchange.exchanges.has("embeddings")) {
      await providerExchange.loadSourceIndexing();
      await providerExchange.mirrorSourceTranscript(completed.session.sessionManager);
    }

    return {
      schema_version: protocol,
      kind: "compaction_finalized",
      reason: completed.reason,
      retry: completed.retry,
      compaction_message,
      messages,
      first_kept_index: prep.firstKeptIndex,
      summary: finalSummary,
    };
  }
return { prepareCompaction, finalizeCompaction, admitSourceRecoveryEvents, createReplaySessionManager,
  reset() { sourceCompactionSnapshot = null; sourceOverflowRecoveryAttempts = 0; sourceLastAdmissionTimestamp = null; },
  get snapshot() { return sourceCompactionSnapshot; },
  get continuation() { return sourceCompactionContinuation; },
};
}
