import { PHASE_SCHEMA, WORKER_API_KEY, sessionState } from "./tool_session.ts";




const replayState = { journalSerial: 0, journalView: [] as Array<{ entryId: string; message: Record<string, unknown> }> };



function appendReplayEntry(timestamp: number, append: () => string): string {
  // Stock append owns topology and entry semantics (session-manager.ts:1271-1282,
  // 1333-1354). Only generated metadata is replaced at the replay boundary.
  // The journal is in-memory, so random IDs/time never reach persisted history.
  const generatedId = append();
  const id = `entry_${replayState.journalSerial++}`;
  const snapshot = sessionState.session.sessionManager.captureState();
  const entries = snapshot.entries.map((entry) => entry.id === generatedId
    ? { ...entry, id, timestamp: new Date(timestamp).toISOString() }
    : entry);
  sessionState.session.sessionManager.restoreState({ ...snapshot, entries });
  return id;
}

function synchronizeJournal(messages: Array<Record<string, unknown>>): void {
  if (messages.length < replayState.journalView.length) throw new Error("native history lost journal messages");
  for (let i = 0; i < replayState.journalView.length; i++) {
    if (JSON.stringify(messages[i]) !== JSON.stringify(replayState.journalView[i].message)) {
      throw new Error(`native history diverged from journal at message ${i}`);
    }
  }
  for (let i = replayState.journalView.length; i < messages.length; i++) {
    const message = messages[i];
    if (message.role === "compactionSummary") throw new Error("compaction summary has no committed journal entry");
    if (typeof message.timestamp !== "number") throw new Error("native journal message lacks replay timestamp");
    const entryId = appendReplayEntry(message.timestamp, () => sessionState.session.sessionManager.appendMessage(message));
    replayState.journalView.push({ entryId, message });
  }
  sessionState.session.agent.replaceMessages(messages);
}

class SentinelAbort extends Error {
  constructor(public readonly kind: string) {
    super(`SentinelAbort:${kind}`);
  }
}

type RequestEnvelope = {
  messages: Array<Record<string, unknown>>;
  max_tokens: number | null;
};

function formatRequest(
  ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> },
  options: { maxTokens?: number | null },
  model: any,
  convertMessages: (model: any, context: any, compat: any) => any[],
): RequestEnvelope {
  const wireMessages = convertMessages(model, ctx, model.compat);
  return {
    messages: wireMessages,
    max_tokens: options?.maxTokens ?? null,
  };
}

async function prepareCompactionPhase(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!Array.isArray(payload?.messages)) {
    throw new Error("prepare_compaction requires messages array");
  }

  const {
    DEFAULT_COMPACTION_SETTINGS,
    compact,
    prepareCompaction,
    shouldCompact,
    calculateContextTokens,
    compactionContextTokens,
    estimateTokens,
  } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/compaction.ts`);
  const { convertMessages } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  // The package root is runtime-selected and hash-checked during initialize.
  const { computeNonMessageTokens } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/modes/utils/context-usage.ts`
  );

  if (!sessionState.session?.model) {
    throw new Error("worker must be initialized with production model before prepare_compaction");
  }
  const model = sessionState.session.model;
  const rawContextWindow = payload.context_window ?? sessionState.session.model.contextWindow;
  if (typeof rawContextWindow !== "number" || rawContextWindow <= 0) {
    throw new Error("prepare_compaction missing valid contextWindow");
  }
  const contextWindow = rawContextWindow;
  const modelId = sessionState.session.model.id;

  const settings = {
    ...DEFAULT_COMPACTION_SETTINGS,
    ...(payload.settings || {}),
  };

  let strategy = settings.strategy;
  const isVision = sessionState.session.model.input?.includes("image") === true;
  if (strategy === "snapcompact" && !isVision) {
    strategy = "context-full";
  }
  if (settings.enabled === false || strategy === "off") {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "disabled",
    };
  }

  const reason = payload.reason ?? "threshold";
  synchronizeJournal(payload.messages);
  const entries = sessionState.session.sessionManager.getBranch();
  if (reason === "threshold") {
    // Mid-run: agent-session.ts:9552-9562. Agent end: 9729,9743-9758.
    // Read stock-parsed usage from the native assistant, not wire usage aliases.
    const assistant = payload.messages.findLast((message: { role: string }) => message.role === "assistant");
    if (!assistant || assistant.stopReason === "aborted" || assistant.stopReason === "error") {
      return { schema_version: PHASE_SCHEMA, kind: "compaction_unavailable", reason: "not_triggered" };
    }
    if (payload.checkpoint === "before_request" && settings.midTurnEnabled === false) {
      return { schema_version: PHASE_SCHEMA, kind: "compaction_unavailable", reason: "not_triggered" };
    }
    const latestCompaction = entries.findLast((entry) => entry.type === "compaction");
    // agent-session.ts:9743-9758 discards billing retained across compaction.
    // The conductor's before_request checkpoint also follows agent_end's
    // continuation, before a new assistant has been sampled. Journal metadata
    // is authoritative here; equality is possible with the replay clock.
    const assistantPredatesCompaction = latestCompaction !== undefined &&
      assistant.timestamp <= new Date(latestCompaction.timestamp).getTime();
    const assistantUsageContextTokens = assistantPredatesCompaction ? 0 : calculateContextTokens(assistant.usage);
    // #estimateStoredContextTokens, agent-session.ts:9459-9470.
    const opts = { excludeEncryptedReasoning: true } as const;
    let messagesTokens = 0;
    for (const message of sessionState.session.messages) messagesTokens += estimateTokens(message, opts);
    const storedContextTokens = computeNonMessageTokens(sessionState.session) + messagesTokens;
    const contextTokens = compactionContextTokens(assistantUsageContextTokens, storedContextTokens);
    const triggerSettings = { ...settings, strategy, enabled: true };
    if (!shouldCompact(contextTokens, contextWindow, triggerSettings)) {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_unavailable",
        reason: "not_triggered",
      };
    }
  }

  const effectiveSettings = { ...settings, strategy, enabled: true };
  const preparation = prepareCompaction(entries, effectiveSettings);
  if (!preparation) {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "nothing_to_compact",
    };
  }

  const firstKeptIndex = replayState.journalView.findIndex((entry) => entry.entryId === preparation.firstKeptEntryId);
  if (firstKeptIndex === -1) throw new Error("stock retained boundary is absent from active history");

  let summaryRequest: RequestEnvelope | null = null;
  let turnPrefixRequest: RequestEnvelope | null = null;

  const replayPrepareImpl = async (_m: unknown, ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> }, options: { maxTokens?: number | null }) => {
    const hasHistory = preparation.messagesToSummarize.length > 0 || preparation.previousSummary;
    if (hasHistory && !summaryRequest) {
      summaryRequest = formatRequest(ctx, options, model, convertMessages);
      return { role: "assistant", content: [{ type: "text", text: "placeholder_summary" }], stopReason: "stop" };
    }
    if (preparation.isSplitTurn && preparation.turnPrefixMessages.length > 0 && !turnPrefixRequest) {
      turnPrefixRequest = formatRequest(ctx, options, model, convertMessages);
      return { role: "assistant", content: [{ type: "text", text: "placeholder_turn_prefix" }], stopReason: "stop" };
    }
    throw new SentinelAbort("prepare_captured");
  };
  try {
    await compact(
      preparation,
      model,
      WORKER_API_KEY,
      payload.customInstructions,
      undefined,
      { completeImpl: replayPrepareImpl },
    );
  } catch (err) {
    if (!(err instanceof SentinelAbort)) throw err;
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_prepared",
    first_kept_index: firstKeptIndex,
    preparation: {
      firstKeptIndex,
      firstKeptEntryId: preparation.firstKeptEntryId,
      isSplitTurn: preparation.isSplitTurn,
      tokensBefore: preparation.tokensBefore,
      previousSummary: preparation.previousSummary,
      previousPreserveData: preparation.previousPreserveData,
      messagesToSummarize: preparation.messagesToSummarize,
      turnPrefixMessages: preparation.turnPrefixMessages,
      recentMessages: preparation.recentMessages,
      fileOps: {
        read: Array.from(preparation.fileOps.read),
        written: Array.from(preparation.fileOps.written),
        edited: Array.from(preparation.fileOps.edited),
      },
      settings: effectiveSettings,
      modelId,
      customInstructions: payload.customInstructions,
      // Wire proof: Stock renderCompactionSummaryContext (src/compaction/messages.ts:87-89,
      // src/compaction/prompts/compaction-summary-context.md:1-5) only interpolates the summary string.
      // convertMessageToLlm (src/compaction/messages.ts:190-206) packages it into a user message, and
      // convertMessages (pi-ai/src/providers/openai-completions.ts:1612-1770) projects only
      // ChatCompletionMessageParam objects (role/content/tool_calls), completely dropping message.timestamp.
      // The replay timestamp comes from persisted native history, never wall time.
      timestamp: payload.messages[payload.messages.length - 1].timestamp,
      checkpoint: payload.checkpoint,
      entries,
      journalLeafId: sessionState.session.sessionManager.getLeafId(),
      messages: payload.messages,
      reason,
      contextWindow,
    },
    summary_request: summaryRequest,
    turn_prefix_request: turnPrefixRequest,
  };
}

async function finalizeCompactionPhase(payload: Record<string, any>): Promise<Record<string, any>> {
  const prep = payload?.preparation;
  if (!prep || typeof prep !== "object") {
    throw new Error("finalize_compaction requires preparation object");
  }
  if (!sessionState.session?.model) {
    throw new Error("worker must be initialized with production model before finalize_compaction");
  }
  const model = sessionState.session.model;
  const rawContextWindow = prep.contextWindow ?? payload.context_window ?? sessionState.session.model.contextWindow;
  if (typeof rawContextWindow !== "number" || rawContextWindow <= 0) {
    throw new Error("finalize_compaction missing valid contextWindow");
  }
  const contextWindow = rawContextWindow;

  const {
    compact,
    DEFAULT_COMPACTION_SETTINGS,
    effectiveReserveTokens,
    resolveThresholdTokens,
    compactionContextTokens,
    estimateTokens,
  } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/compaction.ts`);
  const {
    collectShakeRegions,
    applyShakeRegions,
    AGGRESSIVE_SHAKE_CONFIG,
  } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/shake.ts`);
  const { computeNonMessageTokens } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/modes/utils/context-usage.ts`
  );
  const { convertMessages } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  // Dynamic import: pinnedNodeModules path is runtime-selected from discovery environment
  const autoContinuePrompt = (
    await import(
      `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/prompts/system/auto-continue.md`,
      { with: { type: "text" } }
    )
  ).default;

  // Verbatim replica of stock agent-session.ts:10297-10304 (#buildPostCompactionEagerNudges).
  // Checks #createEagerTodoPrelude (10188-10260) and #createEagerTaskPrelude (10262-10286).
  // In this RL config, settings 'todo.eager' and 'task.eager' default to 'default', plan mode is disabled,
  // and no special preludes are active, so this produces [] identically to stock.
  const buildPostCompactionEagerNudges = (_sess: unknown, _ts: number): Array<Record<string, unknown>> => {
    const nudges: Array<Record<string, unknown>> = [];
    const sessObj = _sess && typeof _sess === "object" ? (_sess as Record<string, unknown>) : undefined;
    const settings = sessObj?.settings && typeof sessObj.settings === "object" ? (sessObj.settings as Record<string, unknown>) : undefined;
    const getSetting = typeof settings?.get === "function" ? (k: string) => (settings.get as (key: string) => unknown)(k) : undefined;
    const todoEager = getSetting?.("todo.eager");
    const todosEnabled = getSetting?.("todo.enabled");
    if (todoEager && todoEager !== "default" && todosEnabled) {
      // #createEagerTodoPrelude
    }
    const taskEager = getSetting?.("task.eager");
    if (taskEager === "always") {
      // #createEagerTaskPrelude
    }
    return nudges;
  };

  const summaries: string[] = [];
  if (typeof payload.summary === "string") summaries.push(payload.summary);
  if (typeof payload.turn_prefix_summary === "string") summaries.push(payload.turn_prefix_summary);
  if (typeof payload.short_summary === "string") summaries.push(payload.short_summary);
  else if (typeof payload.shortSummary === "string") summaries.push(payload.shortSummary);
  if (Array.isArray(payload.followup_summaries)) {
    for (const s of payload.followup_summaries) {
      if (typeof s === "string") summaries.push(s);
    }
  }

  let followupRequest: RequestEnvelope | null = null;
  let requestIndex = 0;

  const preparation = {
    firstKeptEntryId: prep.firstKeptEntryId,
    messagesToSummarize: prep.messagesToSummarize ?? [],
    turnPrefixMessages: prep.turnPrefixMessages ?? [],
    recentMessages: prep.recentMessages ?? [],
    isSplitTurn: prep.isSplitTurn ?? false,
    tokensBefore: prep.tokensBefore ?? 0,
    previousSummary: prep.previousSummary,
    previousPreserveData: prep.previousPreserveData,
    settings: prep.settings,
    fileOps: {
      read: new Set<string>(prep.fileOps?.read || []),
      written: new Set<string>(prep.fileOps?.written || []),
      edited: new Set<string>(prep.fileOps?.edited || []),
    },
  };

  const replayFinalizeImpl = async (
    _model: unknown,
    ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> },
    options: { maxTokens?: number | null },
  ) => {
    if (requestIndex < summaries.length) {
      const summaryText = summaries[requestIndex++];
      return {
        role: "assistant",
        content: [{ type: "text", text: summaryText }],
        stopReason: "stop",
      };
    }
    followupRequest = formatRequest(ctx, options, model, convertMessages);
    throw new SentinelAbort("need_followup");
  };

  let compactResult: any = null;
  try {
    compactResult = await compact(
      preparation,
      model,
      WORKER_API_KEY,
      prep.customInstructions,
      undefined,
      { completeImpl: replayFinalizeImpl },
    );
  } catch (err) {
    if (err instanceof SentinelAbort && err.kind === "need_followup") {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_followup_request",
        request: followupRequest,
      };
    }
    throw err;
  }

  const deterministicTs = prep.timestamp;
  if (sessionState.session.sessionManager.getLeafId() !== prep.journalLeafId) {
    throw new Error("compaction preparation no longer matches the persistent journal");
  }
  const compactionId = appendReplayEntry(deterministicTs, () => sessionState.session.sessionManager.appendCompaction(
    compactResult.summary, compactResult.shortSummary, compactResult.firstKeptEntryId,
    compactResult.tokensBefore, compactResult.details, false, compactResult.preserveData,
  ));
  const branchEntries = sessionState.session.sessionManager.getBranch();
  const compactionEntry = sessionState.session.sessionManager.getEntry(compactionId);
  sessionState.session.agent.replaceMessages(sessionState.session.buildDisplaySessionContext().messages);
  const reason = prep.reason ?? payload.reason ?? "threshold";
  const compactionSettings = { ...DEFAULT_COMPACTION_SETTINGS, ...(prep.settings ?? {}) };
  const { createPlanReadMatcher } = await import(
    `${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/plan-mode/plan-protection.ts`
  );
  // agent-session.ts:8674-8677 (#withPlanProtection), 1387 (default reference);
  // plan-protection.ts:25-30 also protects the canonical local://PLAN.md alias.
  const planMatcher = createPlanReadMatcher(() => "local://PLAN.md");

  // Verbatim #estimateStoredContextTokens body, agent-session.ts:9459-9470.
  // `this` is the stock AgentSession; pendingMessages is empty for this replay.
  function estimateStoredContextTokens(this: typeof sessionState.session, pendingMessages = []) {
    const opts = { excludeEncryptedReasoning: true } as const;
    return (
      computeNonMessageTokens(this) +
      this.messages.reduce((sum, msg) => sum + estimateTokens(msg, opts), 0) +
      pendingMessages.reduce((sum, msg) => sum + estimateTokens(msg, opts), 0)
    );
  }

  function computeResidual(sess = sessionState.session): number {
    const messages = sess.buildDisplaySessionContext().messages;
    if (trailingAssistantDropped) messages.pop();
    sess.agent.replaceMessages(messages);
    // Verbatim residual expression from agent-session.ts:11132-11135,11174-11177.
    return compactionContextTokens(
      sess.getContextUsage({ contextWindow })?.tokens ?? 0,
      estimateStoredContextTokens.call(sess),
    );
  }
  function applyReplayShake(items: Parameters<typeof applyShakeRegions>[0]) {
    // shake.ts:408 stamps prunedAt. Supply the replay clock without modifying
    // stock code; dispatch is serialized, and this mutation is synchronous.
    const wallClock = Date.now;
    Date.now = () => deterministicTs;
    try {
      applyShakeRegions(items);
    } finally {
      Date.now = wallClock;
    }
  }

  let retryFits: boolean | undefined = undefined;
  let hasHeadroom = false;
  let trailingAssistantDropped = false;

  if (reason === "overflow") {
    // 1. Drop trailing error assistant (agent-session.ts:11758-11770)
    const messages = sessionState.session.agent.state.messages;
    const last = messages[messages.length - 1];
    if (last?.role === "assistant" && last.stopReason === "error") {
      trailingAssistantDropped = true;
    }

    // 2. Check retry fit (agent-session.ts:11170-11183)
    let residualTokens = computeResidual(sessionState.session);
    const reserveTokens = effectiveReserveTokens(contextWindow, compactionSettings);
    const defaultReserveTokens = Math.floor(contextWindow * 0.15);
    const fitReserveTokens = Math.min(reserveTokens, defaultReserveTokens);
    const fitBudget = Math.max(0, contextWindow - fitReserveTokens);
    retryFits = residualTokens <= fitBudget;

    // 3. Shake rescue if dead-end (agent-session.ts:11778-11784, 11207-11218, 8806-8855)
    if (!retryFits) {
      const config = {
        ...AGGRESSIVE_SHAKE_CONFIG,
        keepBoundaryId: compactionEntry.firstKeptEntryId,
        protectedTools: [...AGGRESSIVE_SHAKE_CONFIG.protectedTools, planMatcher],
      };
      const regions = collectShakeRegions(branchEntries, config);
      if (regions.length > 0) {
        // agent-session.ts:8824, 8870-8881 (#saveShakeArtifact)
        const parts: string[] = [];
        for (let i = 0; i < regions.length; i++) {
          const region = regions[i];
          parts.push(`### region ${i + 1} (${region.label}, ~${region.tokens} tok)`, "", region.originalText, "");
        }
        let artifactId: string | undefined = undefined;
        try {
          artifactId = await sessionState.session.sessionManager.saveArtifact(parts.join("\n"), "shake");
        } catch {}

        // agent-session.ts:8825, 8857-8862 (#shakeElidePlaceholder)
        const replacements = regions.map((region: any, index: number) => {
          if (artifactId) {
            return `[shaken ~${region.tokens} tokens — recover: artifact://${artifactId} (region ${index + 1})]`;
          }
          return `[shaken ~${region.tokens} tokens]`;
        });

        // agent-session.ts:8831-8840
        const items = regions.map((region: any, index: number) => ({
          region,
          replacement: replacements[index],
        }));
        applyReplayShake(items);


        residualTokens = computeResidual(sessionState.session);
        retryFits = residualTokens <= fitBudget;
      }
    }
  } else if (reason === "threshold") {
    // 1. Check headroom (agent-session.ts:11128-11147)
    // agent-session.ts:480 (COMPACTION_RECOVERY_BAND = 0.8)
    const COMPACTION_RECOVERY_BAND = 0.8;
    let residualTokens = computeResidual(sessionState.session);
    const thresholdTokens = resolveThresholdTokens(contextWindow, compactionSettings);
    const recoveryBand = Math.floor(thresholdTokens * COMPACTION_RECOVERY_BAND);
    hasHeadroom = residualTokens <= recoveryBand;

    // 2. Shake rescue if no headroom (agent-session.ts:11800-11806, 11207-11218, 8806-8855)
    if (!hasHeadroom) {
      const config = {
        ...AGGRESSIVE_SHAKE_CONFIG,
        keepBoundaryId: compactionEntry.firstKeptEntryId,
        protectedTools: [...AGGRESSIVE_SHAKE_CONFIG.protectedTools, planMatcher],
      };
      const regions = collectShakeRegions(branchEntries, config);
      if (regions.length > 0) {
        const parts: string[] = [];
        for (let i = 0; i < regions.length; i++) {
          const region = regions[i];
          parts.push(`### region ${i + 1} (${region.label}, ~${region.tokens} tok)`, "", region.originalText, "");
        }
        let artifactId: string | undefined = undefined;
        try {
          artifactId = await sessionState.session.sessionManager.saveArtifact(parts.join("\n"), "shake");
        } catch {}

        const replacements = regions.map((region: any, index: number) => {
          if (artifactId) {
            return `[shaken ~${region.tokens} tokens — recover: artifact://${artifactId} (region ${index + 1})]`;
          }
          return `[shaken ~${region.tokens} tokens]`;
        });

        const items = regions.map((region: any, index: number) => ({
          region,
          replacement: replacements[index],
        }));
        applyReplayShake(items);


        residualTokens = computeResidual(sessionState.session);
        hasHeadroom = residualTokens <= recoveryBand;
      }
    }
  }

  const compactionMessage = {
    role: "compactionSummary",
    summary: compactResult.summary,
    shortSummary: compactResult.shortSummary,
    firstKeptEntryId: compactResult.firstKeptEntryId,
    tokensBefore: compactResult.tokensBefore,
    details: compactResult.details,
    timestamp: deterministicTs,
  };

  const res: Record<string, any> = {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_finalized",
    compaction_message: compactionMessage,
    compaction_entry: compactionMessage,
    first_kept_index: prep.firstKeptIndex,
    summary: compactResult.summary,
    ...(retryFits !== undefined ? { retry: retryFits } : {}),
  };
  const willRetry = reason === "overflow" || reason === "incomplete";
  // compaction.autoContinue defaults to true (src/config/settings-schema.ts:2004);
  // stock reads it from the compaction settings group (agent-session.ts:11262).
  const effectiveAutoContinue = prep.settings.autoContinue !== false;
  const checkpoint = prep.checkpoint;

  // Context-full path, agent-session.ts:11791-11815: only schedule the
  // auto-continue prompt when the rebuilt context has recovery-band headroom.
  if (
    checkpoint === "agent_end" &&
    reason === "threshold" &&
    !willRetry &&
    hasHeadroom &&
    effectiveAutoContinue
  ) {
    const eagerNudges = buildPostCompactionEagerNudges(sessionState.session, deterministicTs);
    const continueTs = deterministicTs + eagerNudges.length + 1;
    const autoContinueMessage = {
      role: "developer",
      content: [{ type: "text", text: autoContinuePrompt }],
      attribution: "agent",
      timestamp: continueTs,
    };
    res.continuation = [...eagerNudges, autoContinueMessage];
  }

  // Rebuild from stock's chronological journal, never from the display view.
  const messages = [compactionMessage, ...sessionState.session.agent.state.messages.slice(1)];
  res.messages = messages;
  replayState.journalView = messages.map((message, index) => {
    if (index === 0) return { entryId: compactionId, message };
    const entry = branchEntries.find((candidate) => candidate.type === "message" && candidate.message === message);
    if (!entry) throw new Error("stock display message has no journal entry");
    return { entryId: entry.id, message };
  });
  return res;
}



export { finalizeCompactionPhase, prepareCompactionPhase, replayState };
