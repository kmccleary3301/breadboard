import { pathToFileURL } from "node:url";
import { resolve } from "node:path";







const TOOL_IDS = new Set(["read", "bash", "edit", "write"]);

function packageImport(nodeModules, packageName, entry) {
  if (nodeModules) {
    return import(pathToFileURL(resolve(nodeModules, packageName, entry)).href);
  }
  if (!entry || entry === "dist/index.js") {
    return import(packageName);
  }
  const base = import.meta.resolve(packageName);
  const sub = entry.startsWith("dist/") ? "./" + entry.slice(5) : "./" + entry;
  return import(new URL(sub, base).href);
}

const nodeModules = process.env.PI_CODING_AGENT_NODE_MODULES;
const codingAgent = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/index.js");
const piAi = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/index.js");
const openaiCompletions = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/providers/openai-completions.js");
const apiRegistryModule = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/api-registry.js");
const eventStreamModule = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/utils/event-stream.js");
const promptModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/system-prompt.js");
const resourceModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/resource-loader.js");
const shellModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/utils/shell.js");
const childProcessModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/utils/child-process.js");
const compactionModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/compaction/index.js");
const messagesModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/messages.js");
const sessionModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/session-manager.js");
const { isContextOverflow } = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/utils/overflow.js");
const { Agent } = await packageImport(nodeModules, "@mariozechner/pi-agent-core", "dist/index.js");
const openaiErrors = await packageImport(nodeModules, "openai", "core/error.mjs");
const openaiValues = await packageImport(nodeModules, "openai", "internal/utils/values.mjs");
const { buildSessionContext, getLatestCompactionEntry } = sessionModule;
const { APIError } = openaiErrors;
const { safeJSON } = openaiValues;
const { registerApiProvider, unregisterApiProviders } = apiRegistryModule;
const { AssistantMessageEventStream } = eventStreamModule;
const {
  prepareCompaction,
  compact,
  DEFAULT_COMPACTION_SETTINGS,
  calculateContextTokens,
  shouldCompact,
  estimateContextTokens,
} = compactionModule;
const { convertToLlm } = messagesModule;
const {
  createBashTool,
  createBashToolDefinition,
  createEditTool,
  createEditToolDefinition,
  createReadTool,
  createReadToolDefinition,
  createWriteTool,
  createWriteToolDefinition,
} = codingAgent;
const { validateToolArguments, parseStreamingJson } = piAi;
const { convertMessages } = openaiCompletions;
const { buildSystemPrompt } = promptModule;
const { loadProjectContextFiles } = resourceModule;
const { getShellConfig, getShellEnv } = shellModule;
const { waitForChildProcess } = childProcessModule;

function fail(message) {
  throw new Error(message);
}

function requiredString(payload, key) {
  const value = payload?.[key];
  if (typeof value !== "string" || !value) fail(`initialize requires ${key}`);
  return value;
}

function modelForProject(modelDef) {
  // The public config already resolves the provider-level api/baseUrl/compat.
  // Verbatim custom-model fields from stock model-registry.js:445-459.
  const defaultCost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
  return {
    id: modelDef.id,
    name: modelDef.name ?? modelDef.id,
    api: modelDef.api,
    provider: modelDef.provider,
    baseUrl: modelDef.baseUrl,
    reasoning: modelDef.reasoning ?? false,
    thinkingLevelMap: modelDef.thinkingLevelMap,
    input: (modelDef.input ?? ["text"]),
    cost: modelDef.cost ?? defaultCost,
    contextWindow: modelDef.contextWindow ?? 128000,
    maxTokens: modelDef.maxTokens ?? 16384,
    headers: undefined,
    compat: modelDef.compat,
  };
}





export { APIError, Agent, AssistantMessageEventStream, DEFAULT_COMPACTION_SETTINGS, TOOL_IDS, buildSessionContext, buildSystemPrompt, calculateContextTokens, compact, convertMessages, convertToLlm, createBashTool, createBashToolDefinition, createEditTool, createEditToolDefinition, createReadTool, createReadToolDefinition, createWriteTool, createWriteToolDefinition, estimateContextTokens, fail, getLatestCompactionEntry, getShellConfig, getShellEnv, isContextOverflow, loadProjectContextFiles, modelForProject, openaiCompletions, parseStreamingJson, prepareCompaction, registerApiProvider, requiredString, safeJSON, sessionModule, shouldCompact, unregisterApiProviders, validateToolArguments, waitForChildProcess };
