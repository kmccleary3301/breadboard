import { PHASE_SCHEMA, WORKER_API_KEY, sessionState } from "./tool_session.ts";



const replayState = { journalMessageId: 0, journalView: [] as unknown[] };
const COMPACTION_RECOVERY_BAND = 0.8; // session-maintenance.ts:215

// The stock SessionManager owns the full episode journal, including entries
// absent from its reduced context. Append glue: agent-session.ts:2734;
// compaction commit: session-maintenance.ts:1859-1878.



async function withJournalMetadata<T>(operation: () => T | Promise<T>, performanceClock?: () => number): Promise<T> {
  const NativeDate = Date;
  const randomUUID = crypto.randomUUID;
  const randomUUIDv7 = Bun.randomUUIDv7;
  const performanceNow = performance.now;
  const timestamp = 1000 + replayState.journalMessageId;
  class ReplayDate extends NativeDate {
    constructor(value: string | number = timestamp) { super(value); }
    static now() { return timestamp; }
  }
  globalThis.Date = ReplayDate as DateConstructor;
  crypto.randomUUID = () => `00000000-0000-4000-8000-${(++replayState.journalMessageId).toString(16).padStart(12, "0")}`;
  Bun.randomUUIDv7 = () => "00000000-0000-7000-8000-000000000000";
  if (performanceClock) performance.now = performanceClock;
  try { return await operation(); }
  finally {
    globalThis.Date = NativeDate;
    crypto.randomUUID = randomUUID;
    Bun.randomUUIDv7 = randomUUIDv7;
    if (performanceClock) performance.now = performanceNow;
  }
}

async function synchronizeHistory(messages: unknown[]): Promise<Array<Record<string, unknown>>> {
  if (messages.length < replayState.journalView.length ||
      replayState.journalView.some((message, index) => JSON.stringify(message) !== JSON.stringify(messages[index]))) {
    throw new Error("OMP history diverged from the committed stock session context");
  }
  await withJournalMetadata(() => {
    for (const message of messages.slice(replayState.journalView.length)) {
      const msg = message as Record<string, unknown>;
      if (msg.role === "compactionSummary") {
        throw new Error("compactionSummary must originate in this episode's stock journal");
      }
      sessionState.session.sessionManager.appendMessage(structuredClone(msg));
    }
  });
  replayState.journalView = structuredClone(messages);
  sessionState.session.agent.replaceMessages(structuredClone(messages));
  return sessionState.session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
    entry.type === "message" || entry.type === "compaction");
}

async function shakeEntries(config: unknown): Promise<{ tokensFreed: number; toolResultsDropped: number; blocksDropped: number }> {
  // Run stock SessionMaintenance.shake via the real SDK session. The manager is
  // in-memory (session-manager.ts:3115-3121); its stock saveArtifact returns a
  // deterministic counter (2128-2136), including the stock recovery link.
  // Deterministic metadata only: session-manager.ts:99, :2302;
  // session-migrations.ts:7; shake.ts:448. Restore clocks/ids before tool work.
  return await withJournalMetadata(async () => {
    sessionState.session.agent.replaceMessages(sessionState.session.sessionManager.buildSessionContext().messages);
    return await sessionState.session.shake("elide", { config });
  });
}
class ReplaySentinelError extends Error {
  readonly isReplaySentinel = true;
  constructor() {
    super("REPLAY_SENTINEL");
  }
}

class ReplayDriver {
  answers: string[];
  capturedCalls: Array<{
    messages: unknown[];
    max_tokens?: number;
    tool_choice?: string;
    request_body: Record<string, unknown>;
    request_body_json: string;
  }> = [];
  callIndex = 0;

  constructor(answers: string[] = []) {
    this.answers = answers;
  }

  completeImpl = async (
    modelParam: unknown,
    ctx: { systemPrompt?: unknown; messages: Array<Record<string, unknown>>; tools?: unknown },
    opts: { maxTokens?: number; toolChoice?: string; reasoning?: unknown },
  ) => {
    const idx = this.callIndex++;
    const targetModel = (modelParam && typeof modelParam === "object") ? modelParam : sessionState.session.agent.state.model;
    const wireMessages = sessionState.convertMessages!(targetModel, ctx, (targetModel as Record<string, unknown>).compat);
    // The hash-verified supplier source root is selected at runtime.
    const { streamSimple } = await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/stream.ts`);
    let wire: Record<string, unknown> | null = null;
    const sentinel = new ReplaySentinelError();
    const stream = streamSimple(targetModel, ctx, {
      ...opts,
      apiKey: WORKER_API_KEY,
      onPayload: (params: Record<string, unknown>) => {
        wire = params;
        throw sentinel;
      },
    });
    for await (const _event of stream) {}
    if (!wire) throw new Error("pinned OMP18 summary request capture failed");
    const callRecord: { messages: unknown[]; max_tokens?: number; tool_choice?: string; request_body: Record<string, unknown>; request_body_json: string } = {
      messages: wireMessages,
      max_tokens: typeof opts?.maxTokens === "number" ? opts.maxTokens : undefined,
      request_body: wire,
      request_body_json: JSON.stringify(wire),
    };
    if (typeof opts?.toolChoice === "string") {
      callRecord.tool_choice = opts.toolChoice;
    }
    this.capturedCalls[idx] = callRecord;
    if (idx < this.answers.length) {
      return {
        role: "assistant",
        content: [{ type: "text", text: this.answers[idx] }],
        stopReason: "stop",
        timestamp: 2000,
      };
    }
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 10);
    await promise;
    throw new ReplaySentinelError();
  };
}

async function buildHandoffContext(entries: Array<Record<string, unknown>>) {
  const handoffPromptText = sessionState.compactionPkg.renderHandoffPrompt(sessionState.compactionPkg.AUTO_HANDOFF_THRESHOLD_FOCUS);
  const agentMessages = sessionState.session.sessionManager.buildSessionContext().messages;
  const handoffSnapshot = [
    ...agentMessages,
    {
      role: "user",
      content: [{ type: "text", text: handoffPromptText }],
      attribution: "agent",
      timestamp: 1000 + agentMessages.length,
    },
  ];
  // session-handoff.ts:150-157: run the stock session conversion and the
  // agent's side-request builder, with the base rather than per-turn prompt.
  // Pin the clock read by the stock date/cwd transform.
  const NativeDate = Date;
  const timestamp = NativeDate.parse(`${sessionState.currentDate}T12:00:00`);
  class ReplayDate extends NativeDate {
    constructor(value: string | number = timestamp) { super(value); }
    static now() { return timestamp; }
  }
  globalThis.Date = ReplayDate as DateConstructor;
  try {
    const handoffLlmMessages = await sessionState.session.convertMessagesToLlm(handoffSnapshot);
    return await sessionState.session.agent.buildSideRequestContext(handoffLlmMessages, sessionState.nativeSystemPrompt);
  } finally {
    globalThis.Date = NativeDate;
  }
}
function compactionCreatedRetryFit(
  compactedMessages: Array<Record<string, unknown>>,
  contextWindow: number,
  compactionSettings: Record<string, unknown>,
  headroom = false,
): boolean {
  const tokenizer = new sessionState.tokenizerPkg.Tokenizer(sessionState.session.model);
  const nonMessageTokens = sessionState.computeNonMessageTokensFn!(sessionState.session, tokenizer);
  // session-stats.ts:289-291: after compaction (unanchored), getContextUsage returns nonMessageTokens + tokenizer.countMessages(messages)
  const providerUsageTokens = nonMessageTokens + tokenizer.countMessages(compactedMessages);
  const storedContextTokens = nonMessageTokens + tokenizer.countMessages(compactedMessages, { excludeEncryptedReasoning: true });
  // session-maintenance.ts:3088-3091
  const residualTokens = sessionState.compactionPkg.compactionContextTokens(providerUsageTokens, storedContextTokens);
  // session-maintenance.ts:3039-3053 and :3088-3093.
  const fitBudget = headroom
    ? Math.floor(sessionState.compactionPkg.resolveThresholdTokens(contextWindow, compactionSettings) * COMPACTION_RECOVERY_BAND)
    : Math.max(0, contextWindow - sessionState.compactionPkg.resolveBudgetReserveTokens(contextWindow, compactionSettings));
  return residualTokens <= fitBudget;
}

// session-maintenance.ts:3131-3162 (#rescueCompactionDeadEnd)
async function rescueCompactionDeadEnd(
  contextWindow: number,
  compactionSettings: Record<string, unknown>,
  options: { skipElide: boolean; headroom?: boolean },
): Promise<{ success: boolean; messages?: Array<Record<string, unknown>> }> {
  if (options.skipElide) return { success: false };
  const result = await shakeEntries(sessionState.shakePkg.RESCUE_SHAKE_CONFIG);
  if (result.toolResultsDropped + result.blocksDropped === 0) return { success: false };
  const postRescueMessages = sessionState.session.sessionManager.buildSessionContext().messages;
  if (compactionCreatedRetryFit(postRescueMessages, contextWindow, compactionSettings, options.headroom)) {
    return { success: true, messages: postRescueMessages };
  }
  return { success: false };
}

async function commitStockCompaction(prep: Record<string, unknown>, payload: Record<string, unknown>): Promise<void> {
  for (const [key, value] of Object.entries(prep.settings as Record<string, unknown>)) {
    sessionState.session.settings.set(`compaction.${key}`, value);
  }
  const answers = [payload.summary, payload.turn_prefix_summary,
    ...(Array.isArray(payload.followup_summaries) ? payload.followup_summaries : [])]
    .filter((answer): answer is string => typeof answer === "string");
  const nativeFetch = globalThis.fetch;
  let answerIndex = 0;
  await withJournalMetadata(async () => {
    const replayDate = Date;
    // Provider transport is the only replay seam. The source's real
    // SessionMaintenance commits details, method, kept-entry IDs and tokensAfter
    // (session-maintenance.ts:1859-1878; public entry points :775, :1561).
    globalThis.fetch = (async () => {
      if (answerIndex === answers.length) throw new Error("stock compaction replay answers exhausted");
      const chunk = { id: "compaction-replay", object: "chat.completion.chunk",
        created: 2, model: sessionState.session.model.id, choices: [{
          index: 0, delta: { role: "assistant", content: answers[answerIndex++] }, finish_reason: "stop",
        }] };
      globalThis.Date = replayDate;
      return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`,
        { headers: { "content-type": "text/event-stream" } });
    }) as typeof fetch;
    try {
      if (prep.method === "handoff") {
        class HandoffDate extends replayDate {
          constructor(value: string | number = replayDate.parse(`${sessionState.currentDate}T12:00:00`)) { super(value); }
        }
        globalThis.Date = HandoffDate as DateConstructor;
        await sessionState.session.handoff(sessionState.compactionPkg.AUTO_HANDOFF_THRESHOLD_FOCUS);
      } else {
        await sessionState.session.compact();
      }
    } finally { globalThis.fetch = nativeFetch; globalThis.Date = replayDate; }
  });
}

async function prepareCompactionPhase(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!sessionState.workerCompactionEnabled) {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "compaction_disabled",
    };
  }
  const rawMessages = Array.isArray(payload.messages) ? payload.messages : [];
  const entries = await synchronizeHistory(rawMessages);
  const settings = {
    ...sessionState.session.settings.getGroup("compaction"),
    ...(typeof payload.settings === "object" && payload.settings !== null ? payload.settings : {}),
  };
  const model = sessionState.session.agent.state.model;
  let tokenizer = new sessionState.tokenizerPkg.Tokenizer(model);
  if (typeof payload.context_window !== "number" || payload.context_window <= 0 || !Number.isFinite(payload.context_window)) {
    throw new Error("prepare_compaction requires positive context_window");
  }
  const contextWindow = payload.context_window;
  // Stock invalidates an assistant's pre-rewrite bill after a compaction
  // (session-maintenance.ts:2465-2477), including auto-continue checkpoints.
  const lastAssistantIndex = entries.findLastIndex(entry =>
    entry.type === "message" && (entry.message as Record<string, unknown>).role === "assistant");
  const lastCompactionIndex = entries.findLastIndex(entry => entry.type === "compaction");
  const usage = lastAssistantIndex <= lastCompactionIndex ? undefined
    : payload.usage && typeof payload.usage === "object"
      ? sessionState.parseChunkUsage(payload.usage, model, undefined, 1000 + entries.length)
      : (entries[lastAssistantIndex].message as Record<string, unknown>).usage;
  // Verbatim stored-context floor, session-maintenance.ts:1924-1936, :2115-2118.
  const storedTokens = sessionState.computeNonMessageTokensFn(sessionState.session, tokenizer) +
    tokenizer.countMessages(sessionState.session.agent.state.messages, { excludeEncryptedReasoning: true });
  const contextTokens = usage
    ? sessionState.compactionPkg.compactionContextTokens(sessionState.compactionPkg.calculateContextTokens(usage), storedTokens)
    : storedTokens;

  const reason = typeof payload.reason === "string" ? payload.reason : "threshold";
  // The error remains in the source journal, but checkCompaction removes it
  // from the active context before recovery (session-maintenance.ts:2277).
  if (reason === "overflow") {
    const lastMessage = rawMessages.at(-1);
    if (lastMessage && typeof lastMessage === "object" &&
        "role" in lastMessage && lastMessage.role === "assistant" &&
        "stopReason" in lastMessage && lastMessage.stopReason === "error") {
      sessionState.session.agent.replaceMessages(rawMessages.slice(0, -1));
    }
  }
  if (reason === "threshold") {
    if (!sessionState.compactionPkg.shouldCompact(contextTokens, contextWindow, settings)) {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_unavailable",
        reason: "not_triggered",
      };
    }
  }

  const configuredOrder = settings.methodOrder ?? sessionState.compactionMethods.DEFAULT_COMPACTION_METHOD_ORDER;
  const methods = sessionState.compactionMethods.resolveCompactionMethodOrder(configuredOrder);
  let selectedMethod: string | undefined;
  let fallbackFromShake = false;
  let historyRewritten = false;
  let currentEntries = entries;

  for (const candidate of methods) {
    if (candidate === "remote") {
      if (sessionState.compactionMethods.canUseRemoteCompaction(model, sessionState.compactionMethods.resolveMethodSettings(settings, candidate))) {
        selectedMethod = candidate;
        break;
      }
      continue;
    }
    if (candidate === "snapcompact") {
      if (model?.input?.includes("image") === true) {
        selectedMethod = candidate;
        break;
      }
      continue;
    }
    if (candidate === "handoff") {
      if (reason === "overflow") continue;
      selectedMethod = candidate;
      break;
    }
    if (candidate === "shake") {
      const shakeConfig = {
        ...sessionState.shakePkg.DEFAULT_SHAKE_CONFIG,
        ...(typeof settings.shake === "object" && settings.shake !== null ? settings.shake : {}),
      };
      const result = await shakeEntries(shakeConfig);
      const tokensFreed = result.tokensFreed;
      historyRewritten ||= result.toolResultsDropped + result.blocksDropped > 0;
      currentEntries = sessionState.session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
        entry.type === "message" || entry.type === "compaction");
      // SDK and source imports have distinct message-cache registries; a
      // rewritten journal needs fresh local estimates.
      tokenizer = new sessionState.tokenizerPkg.Tokenizer(model);
      const reclaimed = result.toolResultsDropped + result.blocksDropped > 0;
      let stillOverThreshold = false;
      if (contextWindow > 0) {
        const postShakeTokens = Math.max(0, contextTokens - tokensFreed);
        const thresholdTokens = sessionState.compactionPkg.resolveThresholdTokens(contextWindow, settings);
        const recoveryBand = Math.floor(thresholdTokens * COMPACTION_RECOVERY_BAND);
        stillOverThreshold = postShakeTokens > recoveryBand;
      }
      const shouldFallBack = reason !== "idle" && ((reason === "overflow" && !reclaimed) || stillOverThreshold);
      if (!shouldFallBack) {
        const shakenMessages = sessionState.session.sessionManager.buildSessionContext().messages;
        return {
          schema_version: PHASE_SCHEMA,
          kind: "compaction_prepared",
           preparation: {
             method: "shake",
             tokensBefore: contextTokens,
             shakenMessages,
            contextWindow,
            checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
            rawMessages,
            settings,
             reason,
           },
          summary_request: null,
          turn_prefix_request: null,
        };
      }
      fallbackFromShake = true;
      continue;
    }
    if (candidate === "soft") {
      selectedMethod = candidate;
      break;
    }
  }

  if (selectedMethod !== "handoff" && selectedMethod !== "soft") {
    selectedMethod = "soft";
  }

  const effectiveSettings = sessionState.compactionMethods.resolveMethodSettings(settings, selectedMethod);
  let preparation = sessionState.compactionPkg.prepareCompaction(currentEntries, effectiveSettings, model, tokenizer);
  // Automatic maintenance rescues a recent turn before abandoning preparation
  // (session-maintenance.ts:3637-3695). Use the source's elide operation, including
  // its artifact counter, then prepare from the rewritten journal.
  if (!preparation && reason !== "idle" && !fallbackFromShake) {
    const result = await shakeEntries(sessionState.shakePkg.RESCUE_SHAKE_CONFIG);
    if (result.toolResultsDropped + result.blocksDropped > 0) {
      historyRewritten = true;
      currentEntries = sessionState.session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
        entry.type === "message" || entry.type === "compaction");
      tokenizer = new sessionState.tokenizerPkg.Tokenizer(model);
      preparation = sessionState.compactionPkg.prepareCompaction(currentEntries, effectiveSettings, model, tokenizer);
    }
  }
  if (!preparation) {
    const unavailable = {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "nothing_to_compact",
    };
    if (!historyRewritten) return unavailable;
    // Stock keeps an elided branch even when no summary cut becomes possible
    // (session-maintenance.ts:3762-3771); the conductor must commit this view.
    const messages = sessionState.session.agent.state.messages;
    replayState.journalView = structuredClone(messages);
    return { ...unavailable, history_rewritten: true, messages };
  }
  const driver = new ReplayDriver([]);
  if (selectedMethod === "handoff") {
    const handoffContext = await buildHandoffContext(currentEntries);
    try {
      await sessionState.compactionPkg.generateHandoffFromContext(handoffContext, model, {
        completeImpl: driver.completeImpl,
        streamOptions: { apiKey: WORKER_API_KEY },
      });
    } catch (e: unknown) {
      if (!(e instanceof ReplaySentinelError)) throw e;
    }
    const firstKeptIndex = currentEntries.findIndex((e) => e.id === preparation.firstKeptEntryId);
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_prepared",
      preparation: {
         method: "handoff",
         firstKeptIndex,
         firstKeptEntryId: preparation.firstKeptEntryId,
         tokensBefore: preparation.tokensBefore,
        timestamp: 1000 + currentEntries.length,
        contextWindow,
        checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
         rawMessages,
        settings,
        reason,
        fallbackFromShake,
        fileOps: {
          read: Array.from(preparation.fileOps.read),
          written: Array.from(preparation.fileOps.written),
          edited: Array.from(preparation.fileOps.edited),
        },
      },
      summary_request: driver.capturedCalls[0] ?? null,
      turn_prefix_request: null,
    };
  }

  // selectedMethod === "soft"

  try {
    await sessionState.compactionPkg.compact(preparation, model, WORKER_API_KEY, undefined, undefined, {
      completeImpl: driver.completeImpl,
      remoteSystemPrompt: [sessionState.summarizationSystemPromptText],
    });
  } catch (e: unknown) {
    if (!(e instanceof ReplaySentinelError)) throw e;
  }

  const firstKeptIndex = currentEntries.findIndex((e) => e.id === preparation.firstKeptEntryId);
  let summaryRequest: { messages: Array<{ role: string; content: string }>; max_tokens?: number } | null = null;
  let turnPrefixRequest: { messages: Array<{ role: string; content: string }>; max_tokens?: number } | null = null;
  if (preparation.isSplitTurn && preparation.turnPrefixMessages && preparation.turnPrefixMessages.length > 0) {
    if (preparation.messagesToSummarize.length > 0 || preparation.previousSummary) {
      summaryRequest = driver.capturedCalls[0] ?? null;
      turnPrefixRequest = driver.capturedCalls[1] ?? null;
    } else {
      turnPrefixRequest = driver.capturedCalls[0] ?? null;
    }
  } else {
    summaryRequest = driver.capturedCalls[0] ?? null;
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_prepared",
    preparation: {
       method: "soft",
       firstKeptIndex,
       firstKeptEntryId: preparation.firstKeptEntryId,
       tokensBefore: preparation.tokensBefore,
      timestamp: 1000 + currentEntries.length,
      contextWindow,
      checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
       settings,
      rawMessages,
      reason,
      fallbackFromShake,
    },
    summary_request: summaryRequest,
    turn_prefix_request: turnPrefixRequest,
  };
}

async function finalizeCompactionPhase(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const prep = (payload.preparation && typeof payload.preparation === "object") ? (payload.preparation as Record<string, unknown>) : null;
  if (!prep) {
    throw new Error("finalize_compaction requires preparation");
  }
  const summary = typeof payload.summary === "string" ? payload.summary : "";
  const model = sessionState.session.agent.state.model;
  const tokenizer = new sessionState.tokenizerPkg.Tokenizer(model);
  const settings = {
    ...sessionState.session.settings.getGroup("compaction"),
    ...(typeof prep.settings === "object" && prep.settings !== null ? prep.settings : {}),
  };
  const contextWindow = prep.contextWindow;
  if (typeof contextWindow !== "number" || !Number.isFinite(contextWindow) || contextWindow <= 0) {
    throw new Error("finalize_compaction requires positive context_window");
  }
  const isOverflow = prep.reason === "overflow";
 
   let compactionResult: {
    messages?: Array<Record<string, unknown>>;
    compactionMessage?: Record<string, unknown>;
    firstKeptIndex?: number;
    summary?: string;
  };

  if (prep.method === "shake") {
    const shaken = Array.isArray(prep.shakenMessages) ? (prep.shakenMessages as Array<Record<string, unknown>>) : [];
    compactionResult = {
      messages: shaken,
    };
  } else if (prep.method === "handoff") {
    compactionResult = {};
  } else if (prep.method === "soft") {
    const entries = sessionState.session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
      entry.type === "message" || entry.type === "compaction");
    const preparation = sessionState.compactionPkg.prepareCompaction(entries, settings, model, tokenizer);
    if (!preparation) {
      throw new Error("finalize_compaction could not reconstruct preparation");
    }

    const answers: string[] = [];
    if (preparation.isSplitTurn && preparation.turnPrefixMessages && preparation.turnPrefixMessages.length > 0) {
      if (preparation.messagesToSummarize.length > 0 || preparation.previousSummary) {
        if (typeof payload.summary === "string") answers.push(payload.summary);
        if (typeof payload.turn_prefix_summary === "string") answers.push(payload.turn_prefix_summary);
      } else {
        if (typeof payload.turn_prefix_summary === "string") answers.push(payload.turn_prefix_summary);
      }
    } else {
      if (typeof payload.summary === "string") answers.push(payload.summary);
    }
    const followups = Array.isArray(payload.followup_summaries) ? (payload.followup_summaries as unknown[]) : [];
    for (const f of followups) {
      if (typeof f === "string") answers.push(f);
    }

    const driver = new ReplayDriver(answers);
    let compactResult: Record<string, unknown> | null = null;
    try {
      compactResult = (await sessionState.compactionPkg.compact(preparation, model, WORKER_API_KEY, undefined, undefined, {
        completeImpl: driver.completeImpl,
        remoteSystemPrompt: [sessionState.summarizationSystemPromptText],
      })) as Record<string, unknown>;
    } catch (e: unknown) {
      if (!(e instanceof ReplaySentinelError)) throw e;
    }

    if (!compactResult) {
      const nextCall = driver.capturedCalls[answers.length];
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_followup_request",
        request: nextCall,
      };
    }

    compactionResult = {};
  } else {
    throw new Error(`unknown compaction method: ${String(prep.method)}`);
  }
  let retry = true;
  let finalMessages = compactionResult.messages;
  if (!finalMessages) {
    await commitStockCompaction(prep, payload);
    const entry = sessionState.session.sessionManager.getBranch().findLast(
      (item: Record<string, unknown>) => item.type === "compaction");
    if (!entry) throw new Error("stock maintenance did not commit a compaction");
    compactionResult.compactionMessage = {
      role: "compactionSummary", summary: entry.summary, shortSummary: entry.shortSummary,
      tokensBefore: entry.tokensBefore, firstKeptEntryId: entry.firstKeptEntryId,
      details: entry.details, method: entry.method, timestamp: Date.parse(entry.timestamp),
    };
    compactionResult.firstKeptIndex = sessionState.session.sessionManager.getBranch()
      .filter((item: Record<string, unknown>) => item.type === "message" || item.type === "compaction")
      .findIndex((item: Record<string, unknown>) => item.id === entry.firstKeptEntryId);
    compactionResult.summary = entry.summary;
    finalMessages = sessionState.session.sessionManager.buildSessionContext().messages;
  }
  if (isOverflow) {
    let compactedMessages = finalMessages;

    // Drop trailing assistant turn with stopReason "error" before retry check (session-maintenance.ts:4380-4392)
    const lastMsg = compactedMessages[compactedMessages.length - 1];
    if (lastMsg?.role === "assistant" && lastMsg.stopReason === "error") {
      compactedMessages = compactedMessages.slice(0, -1);
    }
    finalMessages = compactedMessages;

    let retryFits = compactionCreatedRetryFit(compactedMessages, contextWindow, settings);
    if (!retryFits) {
      // Rescue pass (session-maintenance.ts:4402 & 3131-3162)
      const skipElide = Boolean(prep.fallbackFromShake || prep.method === "shake");
      const rescueResult = await rescueCompactionDeadEnd(
        contextWindow,
        settings,
        { skipElide },
      );
      if (rescueResult.success && rescueResult.messages) {
        retryFits = true;
        finalMessages = rescueResult.messages;
      }
    }
    retry = retryFits;
  }
  let hasHeadroom = true;
  if (!isOverflow && prep.reason !== "idle") {
    hasHeadroom = compactionCreatedRetryFit(finalMessages, contextWindow, settings, true);
    if (!hasHeadroom) {
      const rescued = await rescueCompactionDeadEnd(
        contextWindow, settings,
        { skipElide: Boolean(prep.fallbackFromShake), headroom: true },
      );
      hasHeadroom = rescued.success;
      if (rescued.messages) finalMessages = rescued.messages;
    }
  }

  const checkpoint = prep.checkpoint ?? payload.checkpoint;
  let continuation: Array<Record<string, unknown>> | undefined;
  const lastAssistant = (prep.rawMessages as Array<Record<string, unknown>> | undefined)?.findLast(m => m.role === "assistant");
  // agent-session.ts:3854-3898: a terminal text answer without an active goal
  // does not continue. RL has no goal/task/todo tools; run the stock eager builder.
  if (checkpoint === "agent_end" && hasHeadroom && settings.autoContinue !== false && !sessionState.queuedMessagesPkg.isTerminalTextAssistantAnswer(lastAssistant)) {
    const tracker = new sessionState.TodoTracker({
      settings: sessionState.session.settings,
      agentKind: () => sessionState.session.agentKind,
      planModeEnabled: () => sessionState.session.planModeEnabled,
      getEnabledToolNames: () => sessionState.tools.map(tool => tool.name),
    });
    const eagerNudges = tracker.buildPostCompactionEagerNudges();
    continuation = [
      ...eagerNudges,
      {
        role: "developer",
        content: [{ type: "text", text: sessionState.autoContinuePromptText }],
        attribution: "agent",
        timestamp: 1000 + (Array.isArray(prep.rawMessages) ? prep.rawMessages.length : 0),
        synthetic: true,
      },
    ];
  }
  sessionState.session.agent.replaceMessages(finalMessages);
  replayState.journalView = structuredClone(finalMessages);

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_finalized",
    messages: finalMessages,
    compaction_message: compactionResult.compactionMessage,
    first_kept_index: compactionResult.firstKeptIndex,
    summary: compactionResult.summary,
    ...(isOverflow ? { retry } : {}),
    ...(continuation ? { continuation } : {}),
  };
}




export { finalizeCompactionPhase, prepareCompactionPhase, replayState, withJournalMetadata };
