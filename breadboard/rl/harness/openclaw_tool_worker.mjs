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
import { pathToFileURL } from "node:url";
import { join, resolve, basename } from "node:path";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { accessSync, constants as fsConstants } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import crypto from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { classifyAgentExecResult, errorEnvelope, exitCodeForEnvelope, formatErrorMessage } from "openclaw:pinned-agent-exec";
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
const MAX_LIVE_PROCESSES = 4;
const TOOL_ORDER = Object.freeze(["edit", "exec", "ls", "process", "read", "write"]);

let workspace = null;
let scopeKey = "openclaw:e4";
let tools = new Map();
let sourceWorkspace = null;
let sourceTransport = null;
let modelConfig = null;
let sourceSkills = null;
let sourceSkillsPrompt = null;
let sourceRuntimePrompt = null;
let sourceProviderPrompt = null;
let sourceAttemptPrompt = null;
let normalizeSourceMessages = null;
let projectSourceRuntimeFragments = null;
let sourceRuntimeFactsContext = null;
let buildSourceRuntimeContextMessage = null;
let convertSourceTranscriptToLlm = null;
let renderSourceFailureCopy = null;
let sourceTimezone = null;
let sourceConfig = null;
let sourceSessionKey = null;
let sourceInitialTimestamp = null;
let sourceExecutionContext = null;
let sourceAcknowledgeResult = null;
let resolveAttemptTranscriptPolicy = null;
let shouldAllowProviderOwnedThinkingReplay = null;
let collectAllowedToolNames = null;
let sanitizeToolUseResultPairing = null;
let sanitizeToolCallIdsForCloudCodeAssist = null;
let builtTools = [];
let verifiedRegistryUrl = null;
let prepared = null;
let preparedContext = null;
let closing = false;
let advertisedTools = new Map();
let capabilityDenials = new Map();
let finalized = false;
// Stock session counter initialization: resource-loader-Bu_pVD2t.mjs:8285.
let sourceOverflowRecoveryAttempts = 0;
let sourceLastAdmissionTimestamp = null;
const pending = new Map();
let sourceSession = null;
let sourceCompaction = null;
let applyAgentCompactionSettingsFromConfig;
let coerceErrorMessage = null;
let validateToolArguments = null;
let parseOpenAICompletionsUsage;
let createEmptyTransportUsage;
let sourceResource = null;
let sourceSessionManager = null;
let sourceAgentCore = null;
let sourceCompactionSnapshot = null;
let sourceContinuationPrompt = null;
let sourceCompactionContinuation = null;
let SAFETY_MARGIN;
let estimateRenderedPromptTokens;
let estimateJsonPayloadTokenPressure;
let estimateMessageTokenPressure;
let IMAGE_BLOCK_TOKENS;
let MAX_COMPACTION_SUMMARY_CHARS;
const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const text = (value) => typeof value === "string" ? value : String(value ?? "");
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
      sourceContinuationPrompt = JSON.parse(payload.subarray(start + declaration.length, end).toString("utf8"));
    }
  }
  verifiedRegistryUrl = bytes["bash-process-registry-DHrULGkz.mjs"];
  const core = await import(bytes["core-coding-tools-DoP9tAh3.mjs"]);
  const sourceBootstrapFiles = await import(bytes["bootstrap-files-BAkC4xBB.mjs"]);
  sourceWorkspace = await import(bytes["workspace-YW5Pl2cf.mjs"]);
  sourceExecutionContext = await import(bytes["tool-execution-context-C6v2UVPI.mjs"]);
  sourceAcknowledgeResult = (await import(bytes["internal-hooks-DUPhyX-W.mjs"])).t;
  sourceTransport = await import(bytes["openai-transport-stream-D950WgL3.mjs"]);
  sourceSkills = (await import(bytes["workspace-skill-loader-BjTKGaFi.mjs"])).i;
  sourceSkillsPrompt = (await import(bytes["workspace-skill-prompt-D3wdQJbf.mjs"])).n;
  sourceRuntimePrompt = (await import(bytes["sandbox-info-BDS1M4pk.mjs"])).i;
  sourceRuntimeFactsContext = (await import(bytes["system-prompt-report-DZJbcDI4.mjs"])).r;
  buildSourceRuntimeContextMessage = (await import(bytes["runtime-context-prompt-DWIcn5Yx.mjs"])).r;
  sourceSession = await import(bytes["session-DVbOtm8K.mjs"]);
  convertSourceTranscriptToLlm = sourceSession.u;
  renderSourceFailureCopy = (await import(bytes["assistant-request-failure-copy-CeEzc8UX.mjs"])).t;
  ({ buildAttemptSystemPrompt: sourceAttemptPrompt, normalizeMessagesForLlmBoundary: normalizeSourceMessages, projectRuntimeContextFragments: projectSourceRuntimeFragments } = await import(pinnedAttemptPrompt));
  sourceProviderPrompt = (await import(bytes["provider-runtime-Cf3GwX2b.mjs"])).z;
  resolveAttemptTranscriptPolicy = (await import(bytes["history-image-prune-BCKEHO6_.mjs"])).s;
  shouldAllowProviderOwnedThinkingReplay = (await import(bytes["helpers-C__iuzW9.mjs"])).S;
  collectAllowedToolNames = (await import(bytes["builtin-openclaw-B-H-7lKk.mjs"])).s;
  sanitizeToolUseResultPairing = (await import(bytes["session-transcript-repair-BqMz_6TX.mjs"])).i;
  sanitizeToolCallIdsForCloudCodeAssist = (await import(bytes["tool-call-id-CnwowhSs.mjs"])).o;
  sourceCompaction = await import(bytes["compaction-DhVoBTx3.mjs"]);
  applyAgentCompactionSettingsFromConfig = (await import(bytes["agent-settings-DcI_VuTd.mjs"])).r;
  coerceErrorMessage = (await import(bytes["error-coercion-C1ZWqtQc.mjs"])).t;
  sourceResource = await import(bytes["resource-loader-Bu_pVD2t.mjs"]);
  sourceSessionManager = (await import(bytes["session-manager-DZHCo5g0.mjs"])).t;
  sourceAgentCore = await import(bytes["agent-core-B_87jlHI.mjs"]);
  SAFETY_MARGIN = (await import(bytes["compaction-planning-CkwWLY-c.mjs"])).r;
  estimateRenderedPromptTokens = sourceResource.At;
  estimateJsonPayloadTokenPressure = sourceResource.Ot;
  estimateMessageTokenPressure = sourceResource.kt;
  IMAGE_BLOCK_TOKENS = sourceCompaction.n;
  MAX_COMPACTION_SUMMARY_CHARS = sourceCompaction.r;
  const sourceRequire = createRequire(bytes["core-coding-tools-DoP9tAh3.mjs"]);
  ({ validateToolArguments } = await import(pathToFileURL(sourceRequire.resolve("@openclaw/ai/validation"))));
  ({ parseOpenAICompletionsUsage, createEmptyTransportUsage } = await import(pathToFileURL(sourceRequire.resolve("@openclaw/ai/transports"))));
  return {
    createCoreCodingTools: core.t,
    resolveBootstrapContextForRun: sourceBootstrapFiles.a,
    buildOpenAICompletionsParams: sourceTransport.t,
    buildInlineProviderModels: (await import(bytes["model.inline-provider-BOrD-NlO.mjs"])).t,
    completeInlineProviderModel: (await import(bytes["model.inline-provider-BOrD-NlO.mjs"])).n,
  };
}

// Private stock glue, verbatim: agent-core-B_87jlHI.mjs:973-981.
function prepareToolCallArguments(tool, toolCall) {
  if (!tool.prepareArguments) return toolCall;
  const preparedArguments = tool.prepareArguments(toolCall.arguments);
  if (preparedArguments === toolCall.arguments) return toolCall;
  return {
    ...toolCall,
    arguments: preparedArguments
  };
}

// Private stock glue, verbatim: agent-core-B_87jlHI.mjs:1365-1373.
function createErrorToolResult(message, details = {}) {
  return {
    content: [{
      type: "text",
      text: message
    }],
    details
  };
}

export async function prepareSourceToolCall(tool, toolCall) {
  if (!validateToolArguments) await verifyAndLoad();
  // Private admission tail, verbatim: agent-core-B_87jlHI.mjs:1067-1093.
  let preparedToolCall;
  try {
    preparedToolCall = prepareToolCallArguments(tool, toolCall);
  } catch (error) {
    return {
      kind: "immediate",
      result: createErrorToolResult(coerceErrorMessage(error)),
      isError: true
    };
  }
  let validatedArgs;
  try {
    validatedArgs = validateToolArguments(tool, preparedToolCall);
  } catch (error) {
    return {
      kind: "immediate",
      result: createErrorToolResult(coerceErrorMessage(error)),
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
function estimateFreshLlmBoundaryTokenPressure(params) {
  const toolTokens = params.tools?.length ? estimateJsonPayloadTokenPressure(params.tools.map(({ name, description, parameters }) => ({
    name,
    description,
    parameters
  }))) : 0;
  return Math.ceil((estimateRenderedPromptTokens(params) + toolTokens + (params.imageCount ?? 0) * IMAGE_BLOCK_TOKENS + params.messages.reduce((total, message) => total + estimateMessageTokenPressure(message), 0)) * SAFETY_MARGIN);
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
      summary: "x".repeat(MAX_COMPACTION_SUMMARY_CHARS),
      tokensBefore: 0,
      timestamp: 0
    }])
  };
}

function episodeSourceTools() {
  return builtTools.map((tool) => {
    const overlay = advertisedTools.get(tool.name);
    return overlay ? { ...tool, description: overlay.description } : tool;
  });
}

function createReplaySessionManager(entries, timestamp) {
  // Selected model path: session-manager-DZHCo5g0.mjs:1477-1483;
  // stock header:380-390. Clone because this factory consumes owned entries.
  const manager = sourceSessionManager.fromSelectedEntries(structuredClone([
    { type: "session", version: 4, id: "compaction-replay", timestamp, cwd: workspace },
    ...entries,
  ]), workspace);
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
  const agent = new sourceAgentCore.t({
    initialState: {
      model: modelConfig, systemPrompt: preparation.systemPrompt,
      // Agent state may omit a length/error still saved in the ledger:
      // resource-loader-Bu_pVD2t.mjs:10107-10109,10183-10186.
      messages: preparation.hasCommittedCheckpoint ? preparation.messages : manager.buildSessionContext().messages,
      tools: episodeSourceTools(),
    },
    streamFn,
  });
  // Invoke the exported session methods without starting the interactive host.
  const session = Object.create(sourceResource.n.prototype);
  session.agent = agent;
  session.sessionManager = manager;
  session.settingsManager = sourceResource.rt.inMemory({ compaction: preparation.settings });
  session.overflowRecoveryAttempts = preparation.overflowRecoveryAttempts;
  // Stock headless ownership: resource-loader-Bu_pVD2t.mjs:8330.
  session.contextOverflowRecoveryOwner = "session";
  session.currentExtensionRunner = new sourceResource.b([], undefined, workspace, manager, undefined);
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
function exactKeys(value, expected, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  const keys = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (keys.length !== wanted.length || keys.some((key, index) => key !== wanted[index])) {
    throw new Error(`${label} keys are not the admitted set`);
  }
}
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

function validateAdvertisement(value) {
  exactKeys(value, ["prompt_removals", "tool_description_replacements", "tools", "capability_denials"], "advertisement");
  if (!Array.isArray(value.prompt_removals) || value.prompt_removals.some((item) => typeof item !== "string")) {
    throw new Error("advertisement.prompt_removals is invalid");
  }
  exactKeys(value.tool_description_replacements, [], "advertisement.tool_description_replacements");
  exactKeys(value.tools, ["exec"], "advertisement.tools");
  exactKeys(value.tools.exec, ["description", "native_sha256"], "advertisement.tools.exec");
  if (typeof value.tools.exec.description !== "string" || typeof value.tools.exec.native_sha256 !== "string") {
    throw new Error("advertisement.tools.exec is invalid");
  }
  exactKeys(value.capability_denials, ["ask", "node"], "advertisement.capability_denials");
  for (const capability of ["ask", "node"]) {
    const denial = value.capability_denials[capability];
    exactKeys(denial, ["schema_version", "capability", "message", "source_ref"], `advertisement.capability_denials.${capability}`);
    for (const key of ["schema_version", "capability", "message", "source_ref"]) {
      if (typeof denial[key] !== "string" || !denial[key]) throw new Error(`advertisement.capability_denials.${capability}.${key} is invalid`);
    }
  }
  advertisedTools = new Map(Object.entries(value.tools));
  capabilityDenials = new Map(
    ["ask", "node"].map((capability) => [capability, value.capability_denials[capability]]),
  );
  return value;
}

function schemaFor(tool) {
  const schema = tool?.parameters ?? tool?.inputSchema ?? tool?.schema ?? { type: "object" };
  const overlay = advertisedTools.get(tool.name);
  let description = text(tool.description);
  if (overlay) {
    if (`sha256:${sha256(Buffer.from(description, "utf8"))}` !== overlay.native_sha256) {
      throw new Error(`advertisement native description hash mismatch for ${tool.name}`);
    }
    description = overlay.description;
  }
  return {
    type: "function",
    function: { name: tool.name, description, parameters: schema },
  };
}

function makeTools(createCoreCodingTools) {
  const built = createCoreCodingTools({
    codingRoot: workspace,
    containmentRoot: workspace,
    includeBaseCodingTools: true,
    includeShellTools: true,
    readOnly: false,
    // core-coding-tools-DoP9tAh3.mjs:1007,1024-1025 derive native read/ls caps.
    modelContextWindowTokens: modelConfig.contextWindow,
    // Pinned supplier default: embedded-agent.runtime-DnOK0ORi.mjs:649
    // `permissionToolPolicy?.workspaceOnly ?? false` and agent-tools-DXxcrXNI.mjs:388
    // `fsConfig.workspaceOnly === true`; the capture config sets neither, so the
    // supplier's file tools echo the model's own path. Containment is the lease envelope.
    workspaceOnly: false,
    execDefaults: {
      host: "gateway",
      timeoutSec: 30,
      security: "full",
      ask: "off",
      allowBackground: true,
      scopeKey,
      cwd: workspace,
    },
    processDefaults: { scopeKey },
  });
  builtTools = built;
  const ordered = TOOL_ORDER.map((name) => built.find((tool) => tool.name === name));
  if (ordered.some((tool) => !tool)) throw new Error("pinned source tool factory did not produce the admitted six-tool set");
  tools = new Map(ordered.map((tool) => [tool.name, tool]));
  return ordered;
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
      ? parseOpenAICompletionsUsage(message.providerUsage, modelConfig)
      : createEmptyTransportUsage() };
  });
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

function projectSourceRequest(messages, buildOpenAICompletionsParams) {
  if (!modelConfig || typeof modelConfig !== "object") throw new Error("model_config is required");
  const system = messages[0];
  const systemPrompt = (system && system.role === "system" && typeof system.content === "string")
    ? system.content
    : "";
  const history = toSourceHistory(system && system.role === "system" ? messages.slice(1) : messages);
  const initialUser = history.find((message) => message.role === "user");
  if (initialUser && !Number.isFinite(initialUser.timestamp)) initialUser.timestamp = sourceInitialTimestamp;
  admitSourceRecoveryEvents(history);
  if (sourceCompactionContinuation) {
    // Caller recovery's internal prompt is projected, never persisted:
    // embedded-agent-CE9KzQvy.mjs:5961-5967,6063-6068.
    history.splice(sourceCompactionContinuation.index, 0, sourceCompactionContinuation.message);
  }
  const normalized = normalizeSourceMessages(history, { timezone: sourceTimezone, includeTimestamp: true });
  const fragments = sourceRuntimeFactsContext({
    cfg: sourceConfig, sessionKey: sourceSessionKey, agentId: "main",
    capabilityToolNames: new Set(TOOL_ORDER),
  });
  const sourceHistory = [
    ...convertSourceTranscriptToLlm(normalized),
    ...convertSourceTranscriptToLlm([
      buildSourceRuntimeContextMessage(projectSourceRuntimeFragments(fragments), fragments),
    ]),
  ];
  const baseTools = builtTools && builtTools.length ? builtTools : Array.from(tools.values());
  const sourceTools = episodeSourceTools();
  // Supplier transcript policy resolution:
  // builtin-openclaw-B-H-7lKk.mjs:18430 transcriptPolicy = resolveAttemptTranscriptPolicy({...})
  // (history-image-prune-BCKEHO6_.mjs:268) -> resolveTranscriptPolicy (helpers-C__iuzW9.mjs:183)
  const transcriptPolicy = resolveAttemptTranscriptPolicy({
    runtimePlan: undefined,
    runtimePlanModelContext: {
      workspaceDir: workspace,
      modelApi: modelConfig?.api,
      model: modelConfig,
    },
    provider: modelConfig?.provider,
    modelId: modelConfig?.id,
    config: sourceConfig,
    env: process.env,
  });
  // builtin-openclaw-B-H-7lKk.mjs:18442: isOpenAIResponsesApi from attempt.model.api
  const isOpenAIResponsesApi = Boolean(
    modelConfig &&
    (modelConfig.api === "openai-responses" ||
     modelConfig.api === "azure-openai-responses" ||
     modelConfig.api === "openai-chatgpt-responses")
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
      ? sanitizeToolUseResultPairing(sourceHistory)
      : sourceHistory;
    projectedHistory = sanitizeToolCallIdsForCloudCodeAssist(
      paired,
      transcriptPolicy.toolCallIdMode,
      {
        preserveNativeAnthropicToolUseIds: transcriptPolicy.preserveNativeAnthropicToolUseIds,
        duplicateToolCallIdStyle: transcriptPolicy.duplicateToolCallIdStyle,
        preserveReplaySafeThinkingToolCallIds: shouldAllowProviderOwnedThinkingReplay({
          modelApi: modelConfig?.api,
          provider: modelConfig?.provider,
          policy: transcriptPolicy,
        }),
        allowedToolNames: collectAllowedToolNames({ tools: baseTools }),
      }
    );
  }
  if (typeof buildOpenAICompletionsParams === "function" && systemPrompt) {
    const params = buildOpenAICompletionsParams(
      modelConfig,
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
    tools: baseTools.map(schemaFor),
  };
}

async function bootstrapContext(resolveBootstrapContextForRun, config, sessionId, sessionKey) {
  const resolved = await resolveBootstrapContextForRun({
    workspaceDir: workspace, config, agentId: "main", sessionId, sessionKey,
  });
  // Non-primary headless runs do not inject onboarding instructions:
  // builtin-openclaw-B-H-7lKk.mjs:1850-1861,1893.
  return resolved.contextFiles.filter((file) => !/(^|[\\/])BOOTSTRAP\.md$/iu.test(file.path.trim()));
}
async function materializeSourcePrompt(ordered, contextFiles, runtimeInputs, packageDir, config, sessionKey) {
  const modelId = modelConfig?.id;
  const provider = modelConfig?.provider;
  const packageRoot = packageDir.endsWith("/dist") ? resolve(packageDir, "..") : packageDir;
  const sessionId = runtimeInputs.session_id;
  if (typeof runtimeInputs.message_timestamp_ms !== "string" || !/^\d{13}$/.test(runtimeInputs.message_timestamp_ms)) {
    throw new Error("declared message_timestamp_ms is required");
  }
  sourceInitialTimestamp = Number(runtimeInputs.message_timestamp_ms);
  if (!Number.isSafeInteger(sourceInitialTimestamp)) throw new Error("declared message_timestamp_ms is invalid");
  sourceConfig = config;
  sourceSessionKey = sessionKey;
  const { runtimeInfo, userTimezone, userDate } = await sourceRuntimePrompt({
    config, agentId: "main", workspaceDir: workspace, cwd: workspace,
    sessionKey, sessionId, model: `${provider}/${modelId}`,
  });
  sourceTimezone = userTimezone;
  const stamped = normalizeSourceMessages(
    [{ role: "user", content: "x", timestamp: sourceInitialTimestamp }],
    { timezone: userTimezone, includeTimestamp: true },
  )[0].content;
  if (typeof stamped !== "string" || !stamped.endsWith("x") || stamped === "x") {
    throw new Error("pinned source did not stamp declared user timestamp");
  }
  const entries = sourceSkills(workspace, {
    agentId: "main", config, bundledSkillsDir: join(packageRoot, "skills"),
  });
  const skillsPrompt = sourceSkillsPrompt({
    workspaceDir: workspace, agentId: "main", config, entries,
    contextTokenBudget: modelConfig.contextWindow,
  });
  const embeddedSystemPrompt = {
    config, agentId: "main", workspaceDir: workspace, runtimeCwd: workspace,
    reasoningLevel: "off", skillsPrompt,
    docsPath: join(packageRoot, "docs"),
    promptMode: "full", promptSurface: "openclaw_main",
    runtimeInfo, userTimezone, userDate, contextFiles,
    tools: ordered, includeMemorySection: true,
  };
  const prompt = sourceAttemptPrompt({
    isRawModelRun: false,
    embeddedSystemPrompt,
    transformProviderSystemPrompt: sourceProviderPrompt,
    providerTransform: {
      provider, config, workspaceDir: workspace,
      context: { config, workspaceDir: workspace, provider, modelId, promptMode: "full", agentId: "main" },
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
  return resolve(workspace, name);
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
      await mkdir(workspace, { recursive: true });
      await writeFile(path, asset.content, { encoding: "utf8", flag: "wx" });
    }
  }
}

function processSnapshot() {
  const result = spawnSync("ps", ["-axo", "pid=,ppid=,pgid=,command="], {
    encoding: "utf8",
    maxBuffer: 4 * 1024 * 1024,
  });
  if (result.status !== 0) throw new Error(`OS process observer failed: ${text(result.stderr)}`);
  return String(result.stdout || "").split("\n").flatMap((line) => {
    const match = line.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);
    return match
      ? [{ pid: Number(match[1]), ppid: Number(match[2]), pgid: Number(match[3]), argv: match[4] }]
      : [];
  });
}

function trackedProcessGroups(sessions) {
  const leaders = new Set(
    sessions
      .map((session) => Number(session?.pid))
      .filter((pid) => Number.isInteger(pid) && pid > 1),
  );
  const snapshot = processSnapshot();
  const members = new Set(leaders);
  let changed = true;
  while (changed) {
    changed = false;
    for (const processInfo of snapshot) {
      if (members.has(processInfo.ppid) && !members.has(processInfo.pid)) {
        members.add(processInfo.pid);
        changed = true;
      }
    }
  }
  const groups = new Map();
  for (const processInfo of snapshot) {
    if (members.has(processInfo.pid)) {
      const group = groups.get(processInfo.pgid) || [];
      group.push(processInfo.pid);
      groups.set(processInfo.pgid, group);
    }
  }
  return groups;
}

function groupIsAbsent(pgid) {
  if (!Number.isInteger(pgid) || pgid <= 1) return false;
  try {
    process.kill(-pgid, 0);
    return false;
  } catch (error) {
    return error?.code === "ESRCH";
  }
}

class OpenClawMarkerObservationError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "OpenClawMarkerObservationError";
  }
}

function markerExecutable() {
  // Sealed launches are Linux-only; elsewhere execPath is the worker's image.
  if (process.platform !== "linux") return process.execPath;
  // A sealed launch runs a memfd, so execPath is "/memfd:... (deleted)".  The
  // kernel's /proc/self/exe link still executes that same sealed image.
  const image = "/proc/self/exe";
  try {
    accessSync(image, fsConstants.X_OK);
  } catch (error) {
    throw new OpenClawMarkerObservationError(
      `OS process observer marker image is not executable: ${text(error?.message ?? error)}`,
      { cause: error },
    );
  }
  return image;
}

function spawnMarker(marker) {
  const executable = markerExecutable();
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(executable, ["-e", "setInterval(() => {}, 1000)", marker], {
      detached: true,
      stdio: "ignore",
    });
    // Without a listener a spawn failure is an uncaught crash, not a receipt.
    child.on("error", (error) => rejectPromise(new OpenClawMarkerObservationError(
      `OS process observer marker spawn failed: ${text(error?.message ?? error)}`,
      { cause: error },
    )));
    child.once("spawn", () => {
      child.unref();
      resolvePromise(child);
    });
  });
}

async function markerObservation() {
  // Observer-only nonce: appears in cleanup evidence, never history or a model request.
  const marker = `bb-openclaw-marker-${process.pid}-${Date.now()}-${crypto.randomUUID()}`;
  const child = await spawnMarker(marker);
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 25));
  const before = processSnapshot().filter((item) => item.argv.includes(marker));
  const leader = before.find((item) => item.pid === child.pid);
  if (!leader || leader.pgid !== child.pid) {
    throw new OpenClawMarkerObservationError("OS process observer marker group was not proven");
  }
  process.kill(-leader.pgid, "SIGTERM");
  let after = [];
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 10));
    after = processSnapshot().filter((item) => item.argv.includes(marker));
    if (after.length === 0 && groupIsAbsent(leader.pgid)) break;
  }
  return { marker_before: before, marker_after: after };
}

async function cleanupScope() {
  const processTool = tools.get("process");
  const observed = [];
  if (!processTool) throw new Error("pinned process tool is unavailable");
  const marker = await markerObservation();
  const list = async () => {
    const result = await processTool.execute("worker-cleanup-list", { action: "list" });
    return Array.isArray(result?.details?.sessions) ? result.details.sessions : [];
  };
  let sessions = await list();
  const tracked = trackedProcessGroups(sessions);
  for (const session of sessions) {
    if (session?.sessionId) {
      observed.push({ sessionId: String(session.sessionId), before: session.status, pid: session.pid ?? null });
      if (session.status === "running" || session.status === "backgrounded") {
        await processTool.execute("worker-cleanup-kill", { action: "kill", sessionId: session.sessionId });
      }
    }
  }
  // Source's registry owns PTY descendants.  OS observation proves that every
  // tracked process group is absent; the registry's empty list alone is not a
  // cleanup receipt.
  if (!verifiedRegistryUrl) throw new Error("pinned process registry was not verified");
  const registry = await import(verifiedRegistryUrl);
  if (typeof registry.x === "function") await registry.x(scopeKey);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    sessions = await list();
    const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded");
    const snapshot = processSnapshot();
    const groups = [...tracked.entries()].map(([pgid, members]) => ({
      pgid,
      leader_exit_observed: members.every((pid) => !snapshot.some((item) => item.pid === pid)),
      group_probe_absent: groupIsAbsent(pgid),
      remaining: snapshot.filter((item) => item.pgid === pgid).map((item) => item.pid),
    }));
    if (
      live.length === 0
      && marker.marker_after.length === 0
      && groups.every((group) => group.leader_exit_observed && group.group_probe_absent && group.remaining.length === 0)
    ) {
      return {
        processes: observed.map((item) => ({ ...item, after: "dead" })),
        process_groups: groups,
        ...marker,
        all_dead: true,
      };
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 25));
  }
  for (const session of sessions) {
    observed.push({
      sessionId: String(session.sessionId ?? ""),
      after: session.status ?? "unknown",
      pid: session.pid ?? null,
    });
  }
  return { processes: observed, process_groups: [...tracked.keys()].map((pgid) => ({ pgid })), ...marker, all_dead: false };
}

function deliveryId() { return `delivery_${crypto.randomUUID()}`; }

async function liveProcessCount() {
  const listed = await tools.get("process").execute("worker-live-limit", { action: "list" });
  const sessions = Array.isArray(listed?.details?.sessions) ? listed.details.sessions : [];
  return sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded").length;
}

async function executePrepared() {
  if (!prepared) throw new Error("execute_batch requires prepare_tools");
  const calls = prepared;
  const context = preparedContext;
  const sequential = calls.some((call) => tools.get(call.name)?.executionMode === "sequential");
  const listed = calls.some((call) => call.name === "exec" && !call.error && call.kind !== "immediate")
    ? await tools.get("process").execute("worker-live-limit", { action: "list" })
    : null;
  const sessions = Array.isArray(listed?.details?.sessions) ? listed.details.sessions : [];
  const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded").length;
  let reserved = 0;
  let completionIndex = 0;
  const run = async (call) => {
    if (call.kind === "immediate") {
      return { id: call.id, completion_index: completionIndex++, ...call.result, isError: call.isError };
    }
    if (call.error) {
      return {
        id: call.id,
        completion_index: completionIndex++,
        content: [{ type: "text", text: call.error }],
        details: call.capability_denial ? { capability_denial: call.capability_denial } : {},
        isError: true,
      };
    }
    const tool = tools.get(call.name);
    if (!tool) {
      return { id: call.id, completion_index: completionIndex++, content: [{ type: "text", text: `undeclared tool ${call.name}` }], details: {}, isError: true };
    }
    if (call.name === "exec" && (sequential ? await liveProcessCount() : live + reserved) >= MAX_LIVE_PROCESSES) {
      return { id: call.id, completion_index: completionIndex++, content: [{ type: "text", text: `OpenClaw live process cap exceeded (${MAX_LIVE_PROCESSES})` }], details: { status: "rejected", maxLiveProcesses: MAX_LIVE_PROCESSES }, isError: true };
    }
    if (call.name === "exec") reserved += 1;
    try {
      const result = await sourceExecutionContext.n({ assistantMessage: context.assistantMessage }, () => tool.execute(call.id, call.arguments));
      const details = result?.details && typeof result.details === "object" ? result.details : {};
      const item = { id: call.id, completion_index: completionIndex++, content: result?.content ?? [], details, isError: Boolean(result?.isError) };
      if (call.name === "process" && call.arguments?.action === "poll" && details.sessionId) {
        const id = deliveryId();
        pending.set(id, { sessionId: String(details.sessionId), result });
        item.delivery_id = id;
      }
      return item;
    } catch (error) {
      return { id: call.id, completion_index: completionIndex++, content: [{ type: "text", text: text(error?.message || error) }], details: {}, isError: true };
    } finally {
      if (call.name === "exec" && sequential) reserved -= 1;
    }
  };
  let results;
  if (sequential) {
    results = [];
    for (const call of calls) results.push(await run(call));
  } else {
    results = await Promise.all(calls.map(run));
  }
  prepared = null;
  preparedContext = null;
  return { schema_version: PROTOCOL, kind: "tool_results", results };
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
  const modelCfg = message.model_config || modelConfig;
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
        sessionId: message.session_id || scopeKey || "",
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
    const advertisement = validateAdvertisement(message.advertisement);
    workspace = resolve(workspacePath);
    scopeKey = typeof message.scopeKey === "string" && message.scopeKey ? message.scopeKey : scopeKey;
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
    sourceCompactionSnapshot = null;
    sourceOverflowRecoveryAttempts = 0;
    sourceLastAdmissionTimestamp = null;
    modelConfig = resolveSourceModel(declaredModel, source.buildInlineProviderModels, source.completeInlineProviderModel);
    const ordered = makeTools(source.createCoreCodingTools);
    const modelId = modelConfig?.id;
    const provider = modelConfig?.provider;
    if (typeof modelId !== "string" || !modelId || typeof provider !== "string" || !provider) {
      throw new Error("model_config provider and id are required for pinned prompt");
    }
    const sessionId = runtimeInputs.session_id;
    if (typeof sessionId !== "string" || !sessionId) throw new Error("declared session_id is required");
    // agent-exec-BAuhpelg.mjs:388 uses this headless scope for runtime facts.
    const sessionKey = `agent:main:agent-exec:${sessionId}`;
    const config = {
      agents: { defaults: { model: { primary: `${provider}/${modelId}` }, workspace } },
      tools: { allow: TOOL_ORDER },
    };
    // agent exec skips template creation (agent-exec-BAuhpelg.mjs:106).
    await sourceWorkspace.d({ dir: workspace, ensureBootstrapFiles: false });
    const bootstrapFiles = await bootstrapContext(source.resolveBootstrapContextForRun, config, sessionId, sessionKey);
    const { prompt: systemPrompt, runtimeFacts } = await materializeSourcePrompt(ordered, bootstrapFiles, runtimeInputs, packageDir, config, sessionKey);
    return {
      schema_version: PROTOCOL,
      kind: "initialized",
      system_prompt: systemPrompt,
      tool_schemas: ordered.map(schemaFor),
      bootstrap: { files: bootstrapFiles, runtime_facts: runtimeFacts, ...runtimeInputs, model_config: modelConfig },
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
  if (phase === "prepare_compaction") {
    if (!modelConfig || typeof modelConfig !== "object") {
      throw new Error("worker is not initialized with model_config");
    }
    if (!sourceCompaction) await verifyAndLoad();
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
    const contextWindow = modelConfig.contextWindow;
    const settingsManager = sourceResource.rt.inMemory({ compaction: settings });
    // Stock defaults and overrides, not worker-invented fallbacks:
    // resource-loader-Bu_pVD2t.mjs:2482-2499; agent-settings-DcI_VuTd.mjs:12-37.
    applyAgentCompactionSettingsFromConfig({
      settingsManager, cfg: sourceConfig, contextTokenBudget: contextWindow,
    });
    const effectiveSettings = settingsManager.getCompactionSettings();


    // Persisted replay clocks/ids replace stock's clock and random entry ids only:
    // session-manager-DZHCo5g0.mjs:995-998,1034-1036.
    const latestTimestamp = rawMessages.reduce(
      (latest, msg) => typeof msg.timestamp === "number" ? Math.max(latest, msg.timestamp) : latest, sourceInitialTimestamp,
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
    const requestBudget = sourceResource.f({
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
      const wire = sourceTransport.t(m, context, options);
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
      return { schema_version: PROTOCOL, kind: "compaction_unavailable", reason: admission.outcome.reason };
    }
    // The real stock planner has now succeeded and reached its provider await.
    // Re-run its exported planner only to serialize protocol preparation metadata.
    const retention = resolveCompactionRetentionBudget(requestBudget, sourceSession.t(entries).messages);
    const prepResult = sourceCompaction.g(entries, effectiveSettings, preparation.sourceReason === "overflow" ? "unresolved" : undefined, {
      budget: { ...retention, estimateTokens: (msg) => estimateCompactionHistoryTokens([msg], requestBudget) },
    });
    if (!prepResult.ok) throw prepResult.error;
    const prep = prepResult.value;
    preparation.firstKeptEntryId = prep.firstKeptEntryId;
    preparation.isSplitTurn = prep.isSplitTurn;
    preparation.tokensBefore = prep.tokensBefore;
    // Budget sizing is stock's private work glue, with only the replay timestamp/id:
    // resource-loader-Bu_pVD2t.mjs:9995-10005.
    const projectReplacement = (result, summary) => sourceSession.t([...entries, {
      ...result, type: "compaction", id: "compaction_replay",
      parentId: entries.at(-1)?.id ?? null, timestamp, summary,
    }]).messages;
    const requestTokenLimit = requestBudget.fixedTokens + requestBudget.pendingTokens + retention.maxTokens;
    const remaining = requestTokenLimit - sourceResource.p(projectReplacement(prep, ""), requestBudget);
    prep.summaryTokenBudget = Math.floor(remaining / SAFETY_MARGIN) - 1;
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
        return { schema_version: PROTOCOL, kind: "compaction_unavailable", reason: next.outcome.reason };
      }
    }
    const firstKeptMessage = entries.find((entry) => entry.id === prep.firstKeptEntryId).message;
    preparation.firstKeptIndex = sourceSession.t(entries).messages.indexOf(firstKeptMessage) + (systemMessage ? 1 : 0);
    const summaryRequest = hasHistory ? capturedRequests[0] : null;
    const turnPrefixRequest = summarizeTurnPrefix ? capturedRequests[hasHistory ? 1 : 0] : null;
    preparation.summaryRequest = summaryRequest;
    preparation.turnPrefixRequest = turnPrefixRequest;

    preparation.prep = { ...prep, fileOps: {
      read: [...prep.fileOps.read], written: [...prep.fileOps.written], edited: [...prep.fileOps.edited],
    } };

    return {
      schema_version: PROTOCOL,
      kind: "compaction_prepared",
      preparation,
      summary_request: summaryRequest,
      turn_prefix_request: turnPrefixRequest,
    };
  }
  if (phase === "finalize_compaction") {
    if (!modelConfig || typeof modelConfig !== "object") {
      throw new Error("worker is not initialized with model_config");
    }
    if (!sourceCompaction) await verifyAndLoad();
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
      const wireParams = sourceTransport.t(m, context, options);
      const request = { messages: wireParams.messages, max_tokens: wireParams.max_completion_tokens ?? wireParams.max_tokens };
      const initialIdx = answers.findIndex((answer) => isDeepStrictEqual(answer.request, request));
      if (initialIdx >= 0) return replaySummaryResponse(answers.splice(initialIdx, 1)[0].summary);
      if (followupIdx < followupAnswers.length) return replaySummaryResponse(followupAnswers[followupIdx++]);
      followup.resolve({ schema_version: PROTOCOL, kind: "compaction_followup_request", request });
      return new Promise(() => {});
    };

    const completed = await Promise.race([runCompactionReplay(prep, streamFn), followup.promise]);
    if (completed.kind === "compaction_followup_request") return completed;
    sourceOverflowRecoveryAttempts = completed.session.overflowRecoveryAttempts;
    if (completed.outcome.status !== "completed") {
      return { schema_version: PROTOCOL, kind: "compaction_unavailable", reason: completed.outcome.reason };
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
        role: "user", content: sourceContinuationPrompt,
        timestamp: new Date(prep.timestamp).getTime(),
      },
    } : null;

    return {
      schema_version: PROTOCOL,
      kind: "compaction_finalized",
      reason: completed.reason,
      retry: completed.retry,
      compaction_message,
      messages,
      first_kept_index: prep.firstKeptIndex,
      summary: finalSummary,
    };
  }
  if (!workspace || tools.size !== TOOL_ORDER.length) throw new Error("worker is not initialized");
  if (phase === "project_request") {
    const projected = projectSourceRequest(
      Array.isArray(message.messages) ? message.messages : [],
      sourceTransport?.t,
    );
    return { schema_version: PROTOCOL, kind: "request", ...projected };
  }
  if (phase === "prepare_tools") {
    if (!Array.isArray(message.calls)) throw new Error("prepare_tools calls must be an array");
    preparedContext = { assistantMessage: {} };
    prepared = await Promise.all(message.calls.map(async (call, index) => {
      const id = String(call?.id ?? `call_${index}`);
      if (!call || typeof call.name !== "string" || !call.name) return { id, name: "", arguments: {}, error: "tool call has no name" };
      const tool = tools.get(call.name);
      if (!tool) return { id, name: call.name, arguments: call.arguments ?? {}, error: `undeclared tool ${call.name}` };
      if (!call.arguments || typeof call.arguments !== "object" || Array.isArray(call.arguments)) return { id, name: call.name, arguments: {}, error: "tool arguments must be an object" };
      const deniedCapability = call.name === "exec"
        ? ["ask", "node"].find((capability) => Object.prototype.hasOwnProperty.call(call.arguments, capability))
        : undefined;
      if (deniedCapability) {
        const denial = capabilityDenials.get(deniedCapability);
        return {
          id,
          name: call.name,
          arguments: call.arguments,
          error: denial.message,
          capability_denial: denial,
        };
      }
      if (call.name === "exec") {
        const seconds = call.arguments.timeoutSeconds;
        if (seconds !== undefined && (
          typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0 || seconds > 30
        )) {
          return { id, name: call.name, arguments: call.arguments, error: "exec timeoutSeconds must be 0 or at most 30" };
        }
      }
      const admission = await prepareSourceToolCall(tool, call);
      if (admission.kind === "immediate") return { id, name: call.name, arguments: call.arguments, ...admission };
      return { id, name: call.name, arguments: admission.args };
    }));
    return {
      schema_version: PROTOCOL,
      kind: "prepared",
      calls: prepared,
    };
  }
  if (phase === "execute_batch") return await executePrepared();
  if (phase === "ack") {
    const id = String(message.delivery_id || "");
    if (!id || !pending.has(id)) throw new Error(`unknown delivery_id ${id}`);
    const record = pending.get(id);
    sourceAcknowledgeResult(record.result);
    for (const [key, delivery] of pending) {
      if (delivery.sessionId === record.sessionId) pending.delete(key);
    }
    return { schema_version: PROTOCOL, kind: "acked", delivery_id: id, session_id: record.sessionId, history_digest: text(message.history_digest) };
  }
  if (phase === "classify_result") {
    let runResult = message.result && typeof message.result === "object"
      ? message.result
      : buildRunResultFromTerminalState(message);
    if (runResult.meta?.error?.kind === "incomplete_turn") {
      const model = runResult.meta.agentMeta;
      const warning = renderSourceFailureCopy({
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
  if (phase === "close") {
    if (closing) throw new Error("worker close already requested");
    const cleanup = await cleanupScope();
    if (!cleanup.all_dead) throw new Error("native process scope did not reach independently observed death");
    closing = true;
    return { schema_version: PROTOCOL, kind: "closed", cleanup };
  }
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
    if (!closing && tools.size) await cleanupScope();
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
