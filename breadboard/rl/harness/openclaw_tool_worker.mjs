import { TOOL_ORDER, createToolSession, prepareToolCallArguments, createErrorToolResult } from "./openclaw_tool_session.mjs";
import { createCompactionController } from "./openclaw_compaction.mjs";
import { createProviderExchange } from "./openclaw_provider_exchange.mjs";
import { pathToFileURL } from "node:url";
import { join, resolve, basename } from "node:path";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import crypto from "node:crypto";
import { classifyAgentExecResult, errorEnvelope, exitCodeForEnvelope, formatErrorMessage } from "openclaw:pinned-agent-exec";

#!/usr/bin/env node
/**
 * OpenClaw 2026.9.4 source-tool and native-compaction phase worker.
 *
 * Loads pinned stock modules, exposes the six admitted source tools and runs
 * exported AgentSession.runCompactionWork (resource-loader-Bu_pVD2t.mjs:9952-10073).
 * The conductor supplies policy answers at the provider seam. The worker owns
 * compaction semantics, the full replacement history and the stock session ledger.
 * Headless admission skips onboarding template creation/injection. The bound
 * model window reaches stock read/ls budgets and skill-catalog compaction.
 * Runtime facts use the agent-exec scope with the declared replay identity.
 * Caller-owned recovery continuation is projected at its transcript boundary,
 * using verified stock prompt bytes and the stock timestamp formatter only.
 * Replay traces retain stock's transport-clamped summary output cap; the sealed
 * policy episode cap remains authoritative for actual BB summary exchanges.
 */










const pinnedAttemptPrompt = "openclaw:pinned-attempt-prompt";

const PROTOCOL = "bb.openclaw-native.v1";
const finalizationOnly = process.argv.length === 3 && process.argv[2] === "--finalize-only";
const DIST = process.env.OPENCLAW_DIST || "/opt/openclaw/dist";
const MODULE_DIGESTS = Object.freeze({
  "core-coding-tools-DoP9tAh3.mjs": "403a72188e3378cc570691083fa9270c05e20dc7e2706c62c5c455457b50dca3",
  "bootstrap-files-BAkC4xBB.mjs": "ff194038a11578027d75ea98570ec71bc253b554c1bda430dcb9cf9eef857a0d",
  "bootstrap-DYYMCrXY.mjs": "e87734ad3d2d4b614a317ddd57565df7ce800aff0b8eaafd565066d905d61e1c",
  "workspace-YW5Pl2cf.mjs": "8201a6b4ee921ac2767e272924488ed7c56c9041d274070490a064968438d5cf",
  "bash-process-registry-DHrULGkz.mjs": "6f8a65296ce1a1e07b0f3d94d2bf65f9e88c5a68b9d01df3eecc1f40f349627e",
  "openai-transport-stream-D950WgL3.mjs": "83fd60ff0760bef6eeabcee42fd219f1213cc9ffe3f65666681f486775032d78",
  "tool-execution-context-C6v2UVPI.mjs": "17e1286b50ee915fa28d5741614a48295994c978e4a64b77ea6b163f674a0082",
  "internal-hooks-DUPhyX-W.mjs": "7288bc46b3e51f1c86e225b14d6baa84d34a806fad0cb37234c4ee5e34b49218",
  "agent-exec-BAuhpelg.mjs": "2e39dbc961337936860849aaaed6a26b734d0c20648093f2bc51a46ebfe9526d",
  "system-prompt-params-BOlFEPMI.mjs": "7e0ba21cff0c164955a15eb8cdeb3ea1a118e55e9f0e97bf8dd44f2c6bda1a4d",
  "workspace-skill-loader-BjTKGaFi.mjs": "230a877bb70d41abf0bbf6af7882e2a2d8060e56eafc4c259e1b853395292f9d",
  "workspace-skill-prompt-D3wdQJbf.mjs": "fecd404cab8af6391fbe52ef7797b8eeaadc63a5104b47b36f69a3583aed36da",
  "sandbox-info-BDS1M4pk.mjs": "470df328a0d4575ad72d011ae9b43ef8dcddc727fd30dcb97b5e920050c64266",
  "provider-runtime-Cf3GwX2b.mjs": "24f24a500e815424e823afa3fc606ced8be03b0e3490be4b9e1ccafb24c0b9a9",
  "builtin-openclaw-B-H-7lKk.mjs": "0a8c813e535c92d03f69bc58381518ba0e6ac6e46f3adda54138c5f668340ea8",
  "system-prompt-report-DZJbcDI4.mjs": "50803825eb86d4f062029c7728bc39fb69ccdef4de83f1e7ab82bb70babb4e08",
  "runtime-context-prompt-DWIcn5Yx.mjs": "66cd0b276bb1b2f7625aae77e1a9e0890fbcc06e249be5f6f0f7cab51692ea84",
  "assistant-request-failure-copy-CeEzc8UX.mjs": "b954236ee1e913406607fea854b2daaf99dcd8049b6faecb978dcca56a020a56",
  "session-DVbOtm8K.mjs": "1e00c482e6fc7333173da95f436ee7ebb8b6975646e2c74a4162f4920fa4c0f7",
  "tool-call-id-CnwowhSs.mjs": "df7cb4ad7daf3cf2a5e85436c83ae2867e5044a53b97b6f8deed57276906c2e7",
  "history-image-prune-BCKEHO6_.mjs": "33ae7d0ea60b3c0f77732bab06facc97a96fff3dcfe8ea742fb3a8346ec161c3",
  "helpers-C__iuzW9.mjs": "97f912914ff788bde4fb842a1ba4db16576c244ac633023a6d7ab1013a216a13",
  "session-transcript-repair-BqMz_6TX.mjs": "e4cf82d1d5e235e2d16549285c5c97f9b756f3b17cca95e4f3646b3c80eebac1",
  "model.inline-provider-BOrD-NlO.mjs": "8feef4dde90987e08cf6abdfdd0d42333b9e0f4f36a763b62aecd163eae746c2",
  "compaction-DhVoBTx3.mjs": "e5cb4b696b6b27cd134073b930c144c156d1abcd0fbd3b3bdc817478806dbec2",
  "agent-compaction-constants-DmXQuPyL.mjs": "1427b21bacd700537340721c16625455394d34bc457a6c6552af7d95945b6266",
  "agent-settings-DcI_VuTd.mjs": "d5af3a814d7cbf8945e0a57353a544cb24100214b7d54939577c4d7ef1c75295",
  "cjk-chars-CGxY6W63.mjs": "f9ecd1677fde596e23b21f883bfe5a1f28df4b5be33df1e9b3e7c5f5a5f25720",
  "record-coerce-DItp3I4t.mjs": "e5acf8ca52794e3fce5b7db301d2425290a09dac816d3965c774f94b8b934dfe",
  "utf16-slice-D_ngcYKd.mjs": "bc9d5d05e504a8c51ede86fbda75f5079388e50e42ac6ecd3885146f0193e944",
  "result-BQGgYouL.mjs": "a1ca6ae792ef0cc288f1a6321dd57f7d45cc97ecff0da30a106121d3f4b75bbe",
  "anthropic-DfKaEfcc.mjs": "357d69d78eefb812873a6f9ab1747229c1b5faf19210529ba4bcd439e99f39a9",
  "src-DDmEryvj.mjs": "172de5b6befa26f6d7f3c91066c08e943e79313ab66fd348af7e3eb64d0aa087",
  "tool-result-pairing-cBi7_uMO.mjs": "6cf899288d2966bb8fe5f7a12988865e616ad8ed9bbdbafbcf1c81ab1bcb4044",
  "error-coercion-C1ZWqtQc.mjs": "00c3f84065dc9d2f55ac7e5ac2c72b7a20b2ff9473340c6a583661673d8f54e9",
  "resource-loader-Bu_pVD2t.mjs": "62b6668ee808bdcedb6a7fc149aee940005c7fafb55427a0a0a313f38a0cc2d8",
  "session-manager-DZHCo5g0.mjs": "de9ca3ee9781b71bb4994e3752e955db2c4a944cc1ab108fb07c9816bfb5ceec",
  "compaction-planning-CkwWLY-c.mjs": "db1ff9d55fda41f0ac6fa93116f75a00ea6ae335a98a3388de7fce975a6f3843",
  "agent-core-B_87jlHI.mjs": "f311dcf52bffaafa4d68436f7337321217c9a08ca5322a3d82176cc12ee2aba7",
  "embedded-agent-CE9KzQvy.mjs": "d764d6270d9227498b5197eef25a598c15541c0269e6e9294a95d079ec88e4fa",
});






































let finalized = false;
// Stock session counter initialization: resource-loader-Bu_pVD2t.mjs:8285.









































const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const text = (value) => typeof value === "string" ? value : String(value ?? "");

const supplier = {
  sourceWorkspace: null,
  sourceTransport: null,
  sourceSkills: null,
  sourceSkillsPrompt: null,
  sourceRuntimePrompt: null,
  sourceProviderPrompt: null,
  sourceAttemptPrompt: null,
  normalizeSourceMessages: null,
  projectSourceRuntimeFragments: null,
  sourceRuntimeFactsContext: null,
  buildSourceRuntimeContextMessage: null,
  convertSourceTranscriptToLlm: null,
  renderSourceFailureCopy: null,
  sourceExecutionContext: null,
  sourceAcknowledgeResult: null,
  resolveAttemptTranscriptPolicy: null,
  shouldAllowProviderOwnedThinkingReplay: null,
  collectAllowedToolNames: null,
  sanitizeToolUseResultPairing: null,
  sanitizeToolCallIdsForCloudCodeAssist: null,
  verifiedRegistryUrl: null,
  sourceSession: null,
  sourceCompaction: null,
  applyAgentCompactionSettingsFromConfig: undefined,
  coerceErrorMessage: null,
  validateToolArguments: null,
  parseOpenAICompletionsUsage: undefined,
  createEmptyTransportUsage: undefined,
  sourceResource: null,
  sourceSessionManager: null,
  sourceAgentCore: null,
  sourceContinuationPrompt: null,
  SAFETY_MARGIN: undefined,
  estimateRenderedPromptTokens: undefined,
  estimateJsonPayloadTokenPressure: undefined,
  estimateMessageTokenPressure: undefined,
  IMAGE_BLOCK_TOKENS: undefined,
  MAX_COMPACTION_SUMMARY_CHARS: undefined,
};
const context = {
  workspace: null,
  scopeKey: "openclaw:e4",
  modelConfig: null,
  sourceTimezone: null,
  sourceConfig: null,
  sourceSessionKey: null,
  sourceInitialTimestamp: null,
  sourceSessionIdentity: null,
};
const toolSession = createToolSession({ supplier, context, protocol: PROTOCOL, prepareSourceToolCall });
const providerExchange = createProviderExchange({ context, dist: DIST, protocol: PROTOCOL, toSourceHistory,
 getCompactionSnapshot: () => compaction.snapshot,
 createReplaySessionManager: (entries, timestamp) => compaction.createReplaySessionManager(entries, timestamp),
});
const compaction = createCompactionController({ supplier, context, protocol: PROTOCOL,
 episodeSourceTools: toolSession.episodeSourceTools, toSourceHistory, verifyAndLoad, providerExchange,
});
async function closeSourceSession() {
 if (toolSession.closing) throw new Error("worker close already requested");
 await providerExchange.close();
 return await toolSession.close();
}

async function verifyAndLoad() {
  const bytes = {};
  for (const [name, expected] of Object.entries(MODULE_DIGESTS)) {
    const path = join(DIST, name);
    const payload = await readFile(path);
    const actual = sha256(payload);
    if (actual !== expected) throw new Error(`pinned OpenClaw dist digest mismatch for ${name}`);
    bytes[name] = pathToFileURL(path);
    if (name === "embedded-agent-CE9KzQvy.mjs") {
      // Private stock data, not a copied prompt: the module exports only the
      // embedded runner and compactor. Digest verification seals this declaration.
      // embedded-agent-CE9KzQvy.mjs:5898,6063-6068.
      const declaration = "\nconst CONTINUATION_PROMPT = ";
      const start = payload.indexOf(declaration);
      const end = payload.indexOf(";\n", start);
      if (start < 0 || end < 0) throw new Error("pinned continuation declaration is missing");
      supplier.sourceContinuationPrompt = JSON.parse(payload.subarray(start + declaration.length, end).toString("utf8"));
    }
  }
  supplier.verifiedRegistryUrl = bytes["bash-process-registry-DHrULGkz.mjs"];
  const core = await import(bytes["core-coding-tools-DoP9tAh3.mjs"]);
  const sourceBootstrapFiles = await import(bytes["bootstrap-files-BAkC4xBB.mjs"]);
  supplier.sourceWorkspace = await import(bytes["workspace-YW5Pl2cf.mjs"]);
  supplier.sourceExecutionContext = await import(bytes["tool-execution-context-C6v2UVPI.mjs"]);
  supplier.sourceAcknowledgeResult = (await import(bytes["internal-hooks-DUPhyX-W.mjs"])).t;
  supplier.sourceTransport = await import(bytes["openai-transport-stream-D950WgL3.mjs"]);
  supplier.sourceSkills = (await import(bytes["workspace-skill-loader-BjTKGaFi.mjs"])).i;
  supplier.sourceSkillsPrompt = (await import(bytes["workspace-skill-prompt-D3wdQJbf.mjs"])).n;
  supplier.sourceRuntimePrompt = (await import(bytes["sandbox-info-BDS1M4pk.mjs"])).i;
  supplier.sourceRuntimeFactsContext = (await import(bytes["system-prompt-report-DZJbcDI4.mjs"])).r;
  supplier.buildSourceRuntimeContextMessage = (await import(bytes["runtime-context-prompt-DWIcn5Yx.mjs"])).r;
  supplier.sourceSession = await import(bytes["session-DVbOtm8K.mjs"]);
  supplier.convertSourceTranscriptToLlm = supplier.sourceSession.u;
  supplier.renderSourceFailureCopy = (await import(bytes["assistant-request-failure-copy-CeEzc8UX.mjs"])).t;
  ({ buildAttemptSystemPrompt: supplier.sourceAttemptPrompt, normalizeMessagesForLlmBoundary: supplier.normalizeSourceMessages, projectRuntimeContextFragments: supplier.projectSourceRuntimeFragments } = await import(pinnedAttemptPrompt));
  supplier.sourceProviderPrompt = (await import(bytes["provider-runtime-Cf3GwX2b.mjs"])).z;
  supplier.resolveAttemptTranscriptPolicy = (await import(bytes["history-image-prune-BCKEHO6_.mjs"])).s;
  supplier.shouldAllowProviderOwnedThinkingReplay = (await import(bytes["helpers-C__iuzW9.mjs"])).S;
  supplier.collectAllowedToolNames = (await import(bytes["builtin-openclaw-B-H-7lKk.mjs"])).s;
  supplier.sanitizeToolUseResultPairing = (await import(bytes["session-transcript-repair-BqMz_6TX.mjs"])).i;
  supplier.sanitizeToolCallIdsForCloudCodeAssist = (await import(bytes["tool-call-id-CnwowhSs.mjs"])).o;
  supplier.sourceCompaction = await import(bytes["compaction-DhVoBTx3.mjs"]);
  supplier.applyAgentCompactionSettingsFromConfig = (await import(bytes["agent-settings-DcI_VuTd.mjs"])).r;
  supplier.coerceErrorMessage = (await import(bytes["error-coercion-C1ZWqtQc.mjs"])).t;
  supplier.sourceResource = await import(bytes["resource-loader-Bu_pVD2t.mjs"]);
  supplier.sourceSessionManager = (await import(bytes["session-manager-DZHCo5g0.mjs"])).t;
  supplier.sourceAgentCore = await import(bytes["agent-core-B_87jlHI.mjs"]);
  supplier.SAFETY_MARGIN = (await import(bytes["compaction-planning-CkwWLY-c.mjs"])).r;
  supplier.estimateRenderedPromptTokens = supplier.sourceResource.At;
  supplier.estimateJsonPayloadTokenPressure = supplier.sourceResource.Ot;
  supplier.estimateMessageTokenPressure = supplier.sourceResource.kt;
  supplier.IMAGE_BLOCK_TOKENS = supplier.sourceCompaction.n;
  supplier.MAX_COMPACTION_SUMMARY_CHARS = supplier.sourceCompaction.r;
  const sourceRequire = createRequire(bytes["core-coding-tools-DoP9tAh3.mjs"]);
  ({ validateToolArguments: supplier.validateToolArguments } = await import(pathToFileURL(sourceRequire.resolve("@openclaw/ai/validation"))));
  ({ parseOpenAICompletionsUsage: supplier.parseOpenAICompletionsUsage, createEmptyTransportUsage: supplier.createEmptyTransportUsage } = await import(pathToFileURL(sourceRequire.resolve("@openclaw/ai/transports"))));
  return {
    createCoreCodingTools: core.t,
    resolveBootstrapContextForRun: sourceBootstrapFiles.a,
    buildOpenAICompletionsParams: supplier.sourceTransport.t,
    buildInlineProviderModels: (await import(bytes["model.inline-provider-BOrD-NlO.mjs"])).t,
    completeInlineProviderModel: (await import(bytes["model.inline-provider-BOrD-NlO.mjs"])).n,
  };
}

// Private stock glue, verbatim: agent-core-B_87jlHI.mjs:973-981.


// Private stock glue, verbatim: agent-core-B_87jlHI.mjs:1365-1373.


export async function prepareSourceToolCall(tool, toolCall) {
  if (!supplier.validateToolArguments) await verifyAndLoad();
  // Private admission tail, verbatim: agent-core-B_87jlHI.mjs:1067-1093.
  let preparedToolCall;
  try {
    preparedToolCall = prepareToolCallArguments(tool, toolCall);
  } catch (error) {
    return {
      kind: "immediate",
      result: createErrorToolResult(supplier.coerceErrorMessage(error)),
      isError: true
    };
  }
  let validatedArgs;
  try {
    validatedArgs = supplier.validateToolArguments(tool, preparedToolCall);
  } catch (error) {
    return {
      kind: "immediate",
      result: createErrorToolResult(supplier.coerceErrorMessage(error)),
      isError: true,
      errorKind: "argument-validation"
    };
  }
  return {
    kind: "prepared",
    toolCall,
    tool,
    args: validatedArgs
  };
}

// Private stock budget glue, verbatim: resource-loader-Bu_pVD2t.mjs:192-199,8875-8900.






























// OpenClaw resolves its model from a models.providers entry; the entry's
// declared members (no compat) reach the pinned resolver, so the pinned
// getCompat derives request compat from provider and baseUrl.
const SOURCE_MODEL_MEMBERS = Object.freeze(["id", "name", "contextWindow", "maxTokens", "input"]);
function resolveSourceModel(declared, buildInlineProviderModels, completeInlineProviderModel) {
  if (!declared) return null;
  const provider = declared.provider;
  if (typeof provider !== "string" || !provider) throw new Error("model_config provider and id are required for pinned prompt");
  const model = {};
  for (const name of SOURCE_MODEL_MEMBERS) if (declared[name] !== undefined) model[name] = declared[name];
  const entry = { models: [model] };
  if (declared.baseUrl !== undefined) entry.baseUrl = declared.baseUrl;
  if (declared.api !== undefined) entry.api = declared.api;
  const resolved = buildInlineProviderModels({ [provider]: entry });
  if (resolved.length !== 1) throw new Error("model_config does not resolve to one source model");
  // Stock completion, including pricing: prepared-model-runtime.configured-Cp6n-J8O.mjs:92-93;
  // model.inline-provider-BOrD-NlO.mjs:107-125. Do not invent zero-cost defaults.
  return completeInlineProviderModel(resolved[0], entry);
}






function toSourceHistory(messages) {
  if (!Array.isArray(messages)) throw new Error("project_request messages must be an array");
  return messages.map((message) => {
    if (!message || typeof message !== "object") throw new Error("project_request message must be an object");
    // Provider-owned raw usage is projected by the real stock parser, without
    // guessing aliases (openai-transport-params-9aPuV5YY.mjs:133-157).
    if (message.role !== "assistant" || Object.hasOwn(message, "usage")) return message;
    // Preserve stock-sanitized retained usage (resource-loader:235-258).
    // Missing provider usage stays stock-empty (assistant-output-tLt4H-iQ.mjs:3-12).
    return { ...message, usage: message.providerUsage
      ? supplier.parseOpenAICompletionsUsage(message.providerUsage, context.modelConfig)
      : supplier.createEmptyTransportUsage() };
  });
}



function projectSourceRequest(messages, buildOpenAICompletionsParams) {
  if (!context.modelConfig || typeof context.modelConfig !== "object") throw new Error("model_config is required");
  const system = messages[0];
  const systemPrompt = (system && system.role === "system" && typeof system.content === "string")
    ? system.content
    : "";
  const history = toSourceHistory(system && system.role === "system" ? messages.slice(1) : messages);
  const initialUser = history.find((message) => message.role === "user");
  if (initialUser && !Number.isFinite(initialUser.timestamp)) initialUser.timestamp = context.sourceInitialTimestamp;
  compaction.admitSourceRecoveryEvents(history);
  if (compaction.continuation) {
    // Caller recovery's internal prompt is projected, never persisted:
    // embedded-agent-CE9KzQvy.mjs:5961-5967,6063-6068.
    history.splice(compaction.continuation.index, 0, compaction.continuation.message);
  }
  const normalized = supplier.normalizeSourceMessages(history, { timezone: context.sourceTimezone, includeTimestamp: true });
  const fragments = supplier.sourceRuntimeFactsContext({
    cfg: context.sourceConfig, sessionKey: context.sourceSessionKey, agentId: "main",
    capabilityToolNames: new Set(TOOL_ORDER),
  });
  const sourceHistory = [
    ...supplier.convertSourceTranscriptToLlm(normalized),
    ...supplier.convertSourceTranscriptToLlm([
      supplier.buildSourceRuntimeContextMessage(supplier.projectSourceRuntimeFragments(fragments), fragments),
    ]),
  ];
  const baseTools = toolSession.builtTools && toolSession.builtTools.length ? toolSession.builtTools : Array.from(toolSession.tools.values());
  const sourceTools = toolSession.episodeSourceTools();
  // Supplier transcript policy resolution:
  // builtin-openclaw-B-H-7lKk.mjs:18430 transcriptPolicy = resolveAttemptTranscriptPolicy({...})
  // (history-image-prune-BCKEHO6_.mjs:268) -> resolveTranscriptPolicy (helpers-C__iuzW9.mjs:183)
  const transcriptPolicy = supplier.resolveAttemptTranscriptPolicy({
    runtimePlan: undefined,
    runtimePlanModelContext: {
      workspaceDir: context.workspace,
      modelApi: context.modelConfig?.api,
      model: context.modelConfig,
    },
    provider: context.modelConfig?.provider,
    modelId: context.modelConfig?.id,
    config: context.sourceConfig,
    env: process.env,
  });
  // builtin-openclaw-B-H-7lKk.mjs:18442: isOpenAIResponsesApi from attempt.model.api
  const isOpenAIResponsesApi = Boolean(
    context.modelConfig &&
    (context.modelConfig.api === "openai-responses" ||
     context.modelConfig.api === "azure-openai-responses" ||
     context.modelConfig.api === "openai-chatgpt-responses")
  );
  // builtin-openclaw-B-H-7lKk.mjs:13915-13916: shouldApplyReplayToolCallIdSanitizer
  const shouldApplyReplayToolCallIdSanitizer = Boolean(
    transcriptPolicy?.sanitizeToolCallIds &&
    Boolean(transcriptPolicy?.toolCallIdMode) &&
    !isOpenAIResponsesApi
  );
  // builtin-openclaw-B-H-7lKk.mjs:13919-13925: sanitizeReplayToolCallIdsForStream
  // builtin-openclaw-B-H-7lKk.mjs:15268-15287
  let projectedHistory = sourceHistory;
  if (shouldApplyReplayToolCallIdSanitizer) {
    // builtin-openclaw-B-H-7lKk.mjs:13920
    const paired = transcriptPolicy.repairToolUseResultPairing
      ? supplier.sanitizeToolUseResultPairing(sourceHistory)
      : sourceHistory;
    projectedHistory = supplier.sanitizeToolCallIdsForCloudCodeAssist(
      paired,
      transcriptPolicy.toolCallIdMode,
      {
        preserveNativeAnthropicToolUseIds: transcriptPolicy.preserveNativeAnthropicToolUseIds,
        duplicateToolCallIdStyle: transcriptPolicy.duplicateToolCallIdStyle,
        preserveReplaySafeThinkingToolCallIds: supplier.shouldAllowProviderOwnedThinkingReplay({
          modelApi: context.modelConfig?.api,
          provider: context.modelConfig?.provider,
          policy: transcriptPolicy,
        }),
        allowedToolNames: supplier.collectAllowedToolNames({ tools: baseTools }),
      }
    );
  }
  if (typeof buildOpenAICompletionsParams === "function" && systemPrompt) {
    const params = buildOpenAICompletionsParams(
      context.modelConfig,
      { systemPrompt, messages: projectedHistory, tools: sourceTools },
      undefined,
    );
    return {
      messages: params.messages || [],
      tools: params.tools || [],
      // Wire body members of the pinned supplier buildOpenAICompletionsParams.
      request_members: Object.keys(params).sort(),
    };
  }
  return {
    messages: projectedHistory,
    tools: baseTools.map(toolSession.schemaFor),
  };
}

async function bootstrapContext(resolveBootstrapContextForRun, config, sessionId, sessionKey) {
  const resolved = await resolveBootstrapContextForRun({
    workspaceDir: context.workspace, config, agentId: "main", sessionId, sessionKey,
  });
  // Non-primary headless runs do not inject onboarding instructions:
  // builtin-openclaw-B-H-7lKk.mjs:1850-1861,1893.
  return resolved.contextFiles.filter((file) => !/(^|[\\/])BOOTSTRAP\.md$/iu.test(file.path.trim()));
}
async function materializeSourcePrompt(ordered, contextFiles, runtimeInputs, packageDir, config, sessionKey) {
  const modelId = context.modelConfig?.id;
  const provider = context.modelConfig?.provider;
  const packageRoot = packageDir.endsWith("/dist") ? resolve(packageDir, "..") : packageDir;
  const sessionId = runtimeInputs.session_id;
  if (typeof runtimeInputs.message_timestamp_ms !== "string" || !/^\d{13}$/.test(runtimeInputs.message_timestamp_ms)) {
    throw new Error("declared message_timestamp_ms is required");
  }
  context.sourceInitialTimestamp = Number(runtimeInputs.message_timestamp_ms);
  if (!Number.isSafeInteger(context.sourceInitialTimestamp)) throw new Error("declared message_timestamp_ms is invalid");
  context.sourceConfig = config;
  context.sourceSessionKey = sessionKey;
  const { runtimeInfo, userTimezone, userDate } = await supplier.sourceRuntimePrompt({
    config, agentId: "main", workspaceDir: context.workspace, cwd: context.workspace,
    sessionKey, sessionId, model: `${provider}/${modelId}`,
  });
  context.sourceTimezone = userTimezone;
  const stamped = supplier.normalizeSourceMessages(
    [{ role: "user", content: "x", timestamp: context.sourceInitialTimestamp }],
    { timezone: userTimezone, includeTimestamp: true },
  )[0].content;
  if (typeof stamped !== "string" || !stamped.endsWith("x") || stamped === "x") {
    throw new Error("pinned source did not stamp declared user timestamp");
  }
  const entries = supplier.sourceSkills(context.workspace, {
    agentId: "main", config, bundledSkillsDir: join(packageRoot, "skills"),
  });
  const skillsPrompt = supplier.sourceSkillsPrompt({
    workspaceDir: context.workspace, agentId: "main", config, entries,
    contextTokenBudget: context.modelConfig.contextWindow,
  });
  const embeddedSystemPrompt = {
    config, agentId: "main", workspaceDir: context.workspace, runtimeCwd: context.workspace,
    reasoningLevel: "off", skillsPrompt,
    docsPath: join(packageRoot, "docs"),
    promptMode: "full", promptSurface: "openclaw_main",
    runtimeInfo, userTimezone, userDate, contextFiles,
    tools: ordered, includeMemorySection: true,
  };
  const prompt = supplier.sourceAttemptPrompt({
    isRawModelRun: false,
    embeddedSystemPrompt,
    transformProviderSystemPrompt: supplier.sourceProviderPrompt,
    providerTransform: {
      provider, config, workspaceDir: context.workspace,
      context: { config, workspaceDir: context.workspace, provider, modelId, promptMode: "full", agentId: "main" },
    },
  }).systemPrompt;
  return {
    prompt,
    runtimeFacts: {
      host: runtimeInfo.host,
      os: runtimeInfo.os,
      arch: runtimeInfo.arch,
      node: runtimeInfo.node,
      session_id: sessionId,
      current_date: userDate,
      timestamp_prefix: stamped.slice(0, -1),
    },
  };
}

function safeBootstrapPath(name) {
  if (!name || basename(name) !== name || name.includes("\\") || name.includes("..")) throw new Error("invalid bootstrap asset name");
  return resolve(context.workspace, name);
}

async function materializeBootstrapAssets(assets) {
  if (!Array.isArray(assets)) return;
  for (const asset of assets) {
    if (!asset || typeof asset.name !== "string" || typeof asset.content !== "string") throw new Error("invalid bootstrap asset");
    const path = safeBootstrapPath(asset.name);
    const actual = sha256(Buffer.from(asset.content, "utf8"));
    if (typeof asset.sha256 !== "string" || asset.sha256 !== `sha256:${actual}`) throw new Error(`bootstrap digest mismatch for ${asset.name}`);
    let existing = null;
    try { existing = await readFile(path, "utf8"); } catch (error) { if (error?.code !== "ENOENT") throw error; }
    if (existing !== null && existing !== asset.content) throw new Error(`bootstrap asset collision for ${asset.name}`);
    if (existing === null) {
      await mkdir(context.workspace, { recursive: true });
      await writeFile(path, asset.content, { encoding: "utf8", flag: "wx" });
    }
  }
}






















function buildRunResultFromTerminalState(message) {
  const messages = Array.isArray(message.messages) ? message.messages : [];
  const payloads = [];
  let lastAssistantText = "";
  for (const msg of messages) {
    if (msg.role === "assistant") {
      if (typeof msg.content === "string" && msg.content) {
        payloads.push({ text: msg.content });
        lastAssistantText = msg.content;
      }
      if (typeof msg.reasoning === "string" && msg.reasoning) {
        payloads.push({ text: msg.reasoning, isReasoning: true });
      }
      if (typeof msg.commentary === "string" && msg.commentary) {
        payloads.push({ text: msg.commentary, isCommentary: true });
      }
    } else if (msg.role === "tool") {
      if (msg.isError) {
        payloads.push({ text: text(msg.content), isError: true });
      }
    }
  }
  const stopReason = message.stop_reason || message.native_stop_reason || (message.timeout ? "timeout" : "stop");
  const isTimeout = message.timeout === true || stopReason === "timeout" || message.termination === "timeout";
  const isError = Boolean(message.error || message.isError || stopReason === "error" || message.termination === "error");
  const errorObj = message.error
    ? (typeof message.error === "object" ? message.error : { message: String(message.error), kind: "agent_error" })
    : undefined;
  const modelCfg = message.model_config || context.modelConfig;
  return {
    payloads: Array.isArray(message.payloads) ? message.payloads : payloads,
    meta: {
      durationMs: Number(message.duration_ms) || 0,
      stopReason: isTimeout ? "timeout" : isError ? "error" : stopReason,
      timeoutPhase: isTimeout ? (message.timeout_phase || "action") : undefined,
      aborted: Boolean(message.aborted),
      error: errorObj,
      finalAssistantVisibleText: message.final_text || (lastAssistantText ? lastAssistantText.trimEnd() : undefined),
      agentMeta: {
        model: modelCfg?.id || modelCfg?.model || null,
        provider: modelCfg?.provider || null,
        sessionId: message.session_id || context.scopeKey || "",
        usage: message.usage || undefined,
        costUsd: message.cost_usd,
        assistantTurns: message.assistant_turns,
        bridgeCalls: message.bridge_calls,
      },
      toolSummary: message.tool_summary || undefined,
    },
  };
}

async function handle(message) {
  const phase = message?.phase || message?.operation;
  if (phase === "initialize") {
    const runtimeInputs = message.runtime_inputs && typeof message.runtime_inputs === "object" ? message.runtime_inputs : {};
    const workspacePath = typeof message.workspace === "string" && message.workspace ? message.workspace : runtimeInputs.cwd;
    if (typeof workspacePath !== "string" || !workspacePath) throw new Error("workspace is required");
    const advertisement = toolSession.validateAdvertisement(message.advertisement);
    context.workspace = resolve(workspacePath);
    context.scopeKey = typeof message.scopeKey === "string" && message.scopeKey ? message.scopeKey : context.scopeKey;
    if (message.model_config && (typeof message.model_config !== "object" || Array.isArray(message.model_config))) {
      throw new Error("model_config must be an object");
    }
    const declaredModel = message.model_config || null;
    let assets = message.bootstrap_assets;
    const packageDir = typeof message.package_dir === "string" && message.package_dir ? message.package_dir : runtimeInputs.package_dir || DIST;
    if (!Array.isArray(assets) && typeof packageDir === "string") {
      assets = [];
      for (const name of ["AGENTS.md", "SOUL.md"]) {
        const content = await readFile(join(packageDir, "bootstrap", name), "utf8");
        assets.push({ name, content, sha256: `sha256:${sha256(Buffer.from(content, "utf8"))}` });
      }
    }
    if (typeof runtimeInputs.home === "string" && runtimeInputs.home) process.env.HOME = runtimeInputs.home;
    if (typeof message.scratch === "string" && message.scratch) {
      process.env.OPENCLAW_STATE_DIR = join(message.scratch, "state");
    }
    await materializeBootstrapAssets(assets);
    const source = await verifyAndLoad();
    compaction.reset();
    context.modelConfig = resolveSourceModel(declaredModel, source.buildInlineProviderModels, source.completeInlineProviderModel);
    const ordered = toolSession.makeTools(source.createCoreCodingTools);
    const modelId = context.modelConfig?.id;
    const provider = context.modelConfig?.provider;
    if (typeof modelId !== "string" || !modelId || typeof provider !== "string" || !provider) {
      throw new Error("model_config provider and id are required for pinned prompt");
    }
    const sessionId = runtimeInputs.session_id;
    if (typeof sessionId !== "string" || !sessionId) throw new Error("declared session_id is required");
    context.sourceSessionIdentity = sessionId;
    providerExchange.configure(message);
    // agent-exec-BAuhpelg.mjs:388 uses this headless scope for runtime facts.
    const sessionKey = `agent:main:agent-exec:${sessionId}`;
    const config = {
      agents: { defaults: { model: { primary: `${provider}/${modelId}` }, workspace: context.workspace } },
      tools: { allow: TOOL_ORDER },
      ...(providerExchange.exchanges.size ? {
        models: { providers: { [provider]: {
          baseUrl: context.modelConfig.baseUrl, api: context.modelConfig.api,
          headers: providerExchange.headers, models: [context.modelConfig],
        } } },
      } : {}),
    };
    // agent exec skips template creation (agent-exec-BAuhpelg.mjs:106).
    await supplier.sourceWorkspace.d({ dir: context.workspace, ensureBootstrapFiles: false });
    const bootstrapFiles = await bootstrapContext(source.resolveBootstrapContextForRun, config, sessionId, sessionKey);
    const { prompt: systemPrompt, runtimeFacts } = await materializeSourcePrompt(ordered, bootstrapFiles, runtimeInputs, packageDir, config, sessionKey);
    return {
      schema_version: PROTOCOL,
      kind: "initialized",
      system_prompt: systemPrompt,
      tool_schemas: ordered.map(toolSession.schemaFor),
      bootstrap: { files: bootstrapFiles, runtime_facts: runtimeFacts, ...runtimeInputs, model_config: context.modelConfig },
      tools: TOOL_ORDER,
    };
  }
  if (phase === "finalize_command_result") {
    const envelope = message.envelope;
    if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)
      || typeof envelope.ok !== "boolean" || typeof envelope.status !== "string"
      || typeof message.sessionId !== "string"
      || !Number.isSafeInteger(message.toolCalls) || message.toolCalls < 0
      || (message.cleanup_error_message !== null && typeof message.cleanup_error_message !== "string")) {
      throw new Error("finalize_command_result payload is invalid");
    }
    let finalEnvelope = envelope;
    let runtimeError = null;
    if (message.cleanup_error_message !== null) {
      const cleanupFailure = new Error(`Agent exec cleanup failed: ${formatErrorMessage(new Error(message.cleanup_error_message))}`);
      if (envelope.ok) finalEnvelope = errorEnvelope(cleanupFailure, message.sessionId);
      else runtimeError = cleanupFailure.message;
    }
    return {
      schema_version: PROTOCOL,
      kind: "finalized_command_result",
      command_result: {
        envelope: finalEnvelope,
        exitCode: exitCodeForEnvelope(finalEnvelope),
        toolCalls: message.toolCalls,
      },
      runtime_error: runtimeError,
    };
  }
  if (phase === "prepare_compaction") return await compaction.prepareCompaction(message);
  if (phase === "finalize_compaction") return await compaction.finalizeCompaction(message);
  if (phase === "drain_provider_exchanges") {
    return await providerExchange.drainSourceProviderExchanges(message.response);
  }
  if (!context.workspace || toolSession.tools.size !== TOOL_ORDER.length) throw new Error("worker is not initialized");
  if (phase === "project_request") {
    if (providerExchange.hasMirroredTranscript) {
      await providerExchange.mirrorSourceHistory(message.messages);
    }
    const projected = projectSourceRequest(
      Array.isArray(message.messages) ? message.messages : [],
      supplier.sourceTransport?.t,
    );
    return { schema_version: PROTOCOL, kind: "request", ...projected };
  }
  if (phase === "prepare_tools") return await toolSession.prepareTools(message);
  if (phase === "execute_batch") return await toolSession.executePrepared();
  if (phase === "ack") return await toolSession.acknowledge(message);
  if (phase === "classify_result") {
    if (providerExchange.hasMirroredTranscript && Array.isArray(message.messages)) {
      await providerExchange.mirrorSourceHistory(message.messages);
    }
    let runResult = message.result && typeof message.result === "object"
      ? message.result
      : buildRunResultFromTerminalState(message);
    if (runResult.meta?.error?.kind === "incomplete_turn") {
      const model = runResult.meta.agentMeta;
      const warning = supplier.renderSourceFailureCopy({
        provider: model?.provider, model: model?.model, reason: "unclassified",
      });
      runResult = {
        ...runResult,
        payloads: [{ text: warning, isError: true, mediaUrl: null }],
        meta: { ...runResult.meta, finalAssistantVisibleText: null },
      };
    }
    const envelope = classifyAgentExecResult(
      runResult,
      Boolean(message.fallback_exhausted || message.fallbackExhausted),
      message.projected_error_payload || message.projectedErrorPayload,
    );
    const exitCode = exitCodeForEnvelope(envelope);
    return {
      schema_version: PROTOCOL,
      kind: "classified_result",
      envelope,
      exit_code: exitCode,
    };
  }
  if (phase === "close") return await closeSourceSession();
  throw new Error(`unknown worker phase ${text(phase)}`);
}

function writeFrame(value) {
  const payload = Buffer.from(JSON.stringify(value), "utf8");
  const prefix = Buffer.allocUnsafe(4);
  prefix.writeUInt32BE(payload.length, 0);
  process.stdout.write(Buffer.concat([prefix, payload]));
}


let shuttingDown = false;
async function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    if (!toolSession.closing && toolSession.tools.size) await toolSession.cleanupScope();
  } finally {
    process.exit(code);
  }
}

let input = Buffer.alloc(0);
let chain = Promise.resolve();
let finishInput;
const inputDone = new Promise((resolvePromise) => { finishInput = resolvePromise; });

async function dispatch(command) {
  try {
    if (finalizationOnly) {
      if (command.operation !== "finalize_command_result" || finalized) {
        throw new Error("finalize-only worker admits one finalization phase");
      }
      finalized = true;
    } else if (command.operation === "finalize_command_result") {
      throw new Error("finalization requires a retired native runtime");
    }
    const payload = command.payload && typeof command.payload === "object" ? command.payload : {};
    const message = { ...payload, phase: command.operation };
    const result = await handle(message);
    writeFrame({ schema_version: "bb.native-worker.rpc.v1", request_id: command.request_id, result });
  } catch (error) {
    // Transport only: forward error type/message to the conductor, never success or defaults.
    const message = error instanceof Error ? error.message : text(error);
    writeFrame({
      schema_version: "bb.native-worker.rpc.v1",
      request_id: command.request_id,
      error: {
        type: error instanceof Error ? error.name : "OpenClawWorkerError",
        message,
      },
    });
  }
}

function drainInput() {
  while (true) {
    if (input.length < 4) return;
    const length = input.readUInt32BE(0);
    if (length > 16 * 1024 * 1024) {
      void shutdown(1);
      return;
    }
    if (input.length < length + 4) return;
    const frame = input.subarray(4, length + 4).toString("utf8");
    input = input.subarray(length + 4);
    let command;
    try {
      command = JSON.parse(frame);
    } catch (error) {
      // Transport only: forward error type/message to the conductor, never success or defaults.
      writeFrame({
        schema_version: "bb.native-worker.rpc.v1",
        request_id: null,
        error: { type: "ProtocolError", message: text(error?.message || error) },
      });
      continue;
    }
    chain = chain.then(() => dispatch(command));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.once("SIGTERM", () => void shutdown(0));
  process.once("SIGINT", () => void shutdown(130));
  process.stdin.on("data", (chunk) => {
    input = Buffer.concat([input, chunk]);
    drainInput();
  });
  process.stdin.on("end", async () => {
    await chain;
    finishInput();
  });
  await inputDone;
  if (finalizationOnly) process.stdout.end();
  else await shutdown(0);
}
