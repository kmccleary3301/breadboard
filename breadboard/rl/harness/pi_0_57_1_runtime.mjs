import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";









const PHASE_SCHEMA_VERSION = "bb.pi-native.v0.57.1";
// The declared `--tools read,bash,edit,write,grep,find,ls` order. Pinned
// sdk.js:125-127 keeps this order as the initial active tool names, and
// agent-session.js:536-564 renders the prompt tool list in the same order.
const TOOL_ORDER = Object.freeze(["read", "bash", "edit", "write", "grep", "find", "ls"]);
const PINNED_VERSIONS = Object.freeze({
  "@mariozechner/pi-coding-agent": "0.57.1",
  "@mariozechner/pi-ai": "0.57.1",
  "@mariozechner/pi-agent-core": "0.57.1",
  openai: "6.26.0",
});
const MODEL_CONFIG_KEYS = Object.freeze([
  "id", "name", "api", "provider", "baseUrl", "reasoning", "input", "cost", "contextWindow", "maxTokens", "compat",
]);
const INITIALIZE_KEYS = Object.freeze([
  "task", "model_config", "advertisement", "runtime_inputs", "workspace", "scratch", "package_dir",
]);
const RUNTIME_INPUT_KEYS = Object.freeze(["cwd", "home", "package_dir"]);
// Pinned streamSimpleOpenAICompletions throws without an API key
// (pi-ai/dist/providers/openai-completions.js:255-258) and createClient needs
// one (:268-274).  The R3 custody models.json declares apiKey "EMPTY"; the key
// only reaches the SDK client, never the request body.
const PROVIDER_API_KEY = "EMPTY";

function fail(message) {
  throw new Error(message);
}

const nodeModules = process.env.PI_CODING_AGENT_NODE_MODULES;
if (typeof nodeModules !== "string" || !nodeModules) fail("PI_CODING_AGENT_NODE_MODULES is required");
if (process.env.PI_NATIVE_WORKER_FRAMED !== "1") fail("pi-tools-0.57.1 requires PI_NATIVE_WORKER_FRAMED=1");

function pinnedImport(packageName, entry) {
  return import(pathToFileURL(resolve(nodeModules, packageName, entry)).href);
}

const codingAgent = await pinnedImport("@mariozechner/pi-coding-agent", "dist/index.js");
const toolsModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/core/tools/index.js");
const promptModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/core/system-prompt.js");
const shellModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/utils/shell.js");
const toolsManagerModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/utils/tools-manager.js");
const piAi = await pinnedImport("@mariozechner/pi-ai", "dist/index.js");
// pi-ai imports "openai" as ESM (package exports "import" -> *.mjs), so the
// error classes come from the same ESM files the pinned SDK client throws.
const openaiErrors = await pinnedImport("openai", "core/error.mjs");
const openaiValues = await pinnedImport("openai", "internal/utils/values.mjs");
const apiRegistryModule = await pinnedImport("@mariozechner/pi-ai", "dist/api-registry.js");
const eventStreamModule = await pinnedImport("@mariozechner/pi-ai", "dist/utils/event-stream.js");
const messagesModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/core/messages.js");
const openaiCompletions = await pinnedImport("@mariozechner/pi-ai", "dist/providers/openai-completions.js");
const compactionModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/core/compaction/index.js");
const { registerApiProvider, unregisterApiProviders } = apiRegistryModule;
const { AssistantMessageEventStream } = eventStreamModule;
const sessionModule = await pinnedImport("@mariozechner/pi-coding-agent", "dist/core/session-manager.js");
const { isContextOverflow } = await pinnedImport("@mariozechner/pi-ai", "dist/utils/overflow.js");
const { Agent } = await pinnedImport("@mariozechner/pi-agent-core", "dist/index.js");
const { buildSessionContext, getLatestCompactionEntry } = sessionModule;
const { convertMessages } = openaiCompletions;
const {
  DEFAULT_COMPACTION_SETTINGS,
  calculateContextTokens,
  estimateContextTokens,
  prepareCompaction,
  compact,
  shouldCompact,
} = compactionModule;
const { DefaultResourceLoader, SettingsManager, convertToLlm } = codingAgent;
const {
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
} = toolsModule;
const { buildSystemPrompt } = promptModule;
const { getShellConfig, getShellEnv } = shellModule;
const { getToolPath } = toolsManagerModule;
const { parseStreamingJson, streamSimpleOpenAICompletions, validateToolArguments } = piAi;
const { APIError } = openaiErrors;
const { safeJSON } = openaiValues;

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requireExactKeys(value, keys, label) {
  if (!isPlainObject(value)) fail(`${label} must be an object`);
  const actual = Object.keys(value);
  if (actual.length !== keys.length || keys.some((key) => !Object.hasOwn(value, key))) {
    fail(`${label} keys must be exactly ${keys.join(", ")}`);
  }
}

function requiredString(payload, key) {
  const value = payload[key];
  if (typeof value !== "string" || !value) fail(`initialize requires ${key}`);
  return value;
}

function verifyPinnedVersions() {
  for (const [packageName, version] of Object.entries(PINNED_VERSIONS)) {
    const manifest = JSON.parse(readFileSync(resolve(nodeModules, packageName, "package.json"), "utf8"));
    if (manifest.name !== packageName || manifest.version !== version) {
      fail(`pinned package ${packageName} must be ${version}`);
    }
  }
}

/** Pinned model object (pi-ai Model<"openai-completions">) from the sealed model_config. */
function modelFromConfig(config) {
  requireExactKeys(config, MODEL_CONFIG_KEYS, "model_config");
  for (const key of ["id", "name", "provider", "baseUrl"]) {
    if (typeof config[key] !== "string" || !config[key]) fail(`model_config.${key} must be a non-empty string`);
  }
  if (config.api !== "openai-completions") fail("model_config.api must be openai-completions");
  if (typeof config.reasoning !== "boolean") fail("model_config.reasoning must be a boolean");
  if (!Array.isArray(config.input) || config.input.some((item) => typeof item !== "string")) {
    fail("model_config.input must be a string list");
  }
  requireExactKeys(config.cost, ["input", "output", "cacheRead", "cacheWrite"], "model_config.cost");
  if (Object.values(config.cost).some((value) => typeof value !== "number")) fail("model_config.cost must be numeric");
  for (const key of ["contextWindow", "maxTokens"]) {
    if (!Number.isInteger(config[key]) || config[key] <= 0) fail(`model_config.${key} must be a positive integer`);
  }
  if (!isPlainObject(config.compat)) fail("model_config.compat must be an object");
  // Stock custom-model construction, model-registry.js:362-375. The public
  // sealed config already resolves provider, api and provider-level baseUrl.
  const defaultCost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
  return {
    id: config.id,
    name: config.name ?? config.id,
    api: config.api,
    provider: config.provider,
    baseUrl: config.baseUrl,
    reasoning: config.reasoning ?? false,
    input: (config.input ?? ["text"]),
    cost: config.cost ?? defaultCost,
    contextWindow: config.contextWindow ?? 128000,
    maxTokens: config.maxTokens ?? 16384,
    headers: undefined,
    compat: config.compat,
  };
}





export { APIError, Agent, AssistantMessageEventStream, DEFAULT_COMPACTION_SETTINGS, DefaultResourceLoader, INITIALIZE_KEYS, PHASE_SCHEMA_VERSION, PROVIDER_API_KEY, RUNTIME_INPUT_KEYS, SettingsManager, TOOL_ORDER, buildSessionContext, buildSystemPrompt, calculateContextTokens, compact, convertMessages, convertToLlm, createBashTool, createEditTool, createFindTool, createGrepTool, createLsTool, createReadTool, createWriteTool, estimateContextTokens, fail, getLatestCompactionEntry, getShellConfig, getShellEnv, getToolPath, isContextOverflow, isPlainObject, modelFromConfig, openaiCompletions, parseStreamingJson, prepareCompaction, registerApiProvider, requireExactKeys, requiredString, safeJSON, sessionModule, shouldCompact, streamSimpleOpenAICompletions, unregisterApiProviders, validateToolArguments, verifyPinnedVersions };
