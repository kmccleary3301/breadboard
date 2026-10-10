#!/usr/bin/env node
/**
 * Pinned Pi 0.57.1 native worker (framed bb.native-worker.rpc.v1 only).
 *
 * Every Pi behavior comes from the pinned packages under
 * PI_CODING_AGENT_NODE_MODULES: @mariozechner/pi-coding-agent@0.57.1,
 * @mariozechner/pi-ai@0.57.1, @mariozechner/pi-agent-core@0.57.1 and
 * openai@6.26.0.  Frame protocol, error envelopes, HOME/TMPDIR handling and
 * process-group cleanup mirror pi_tools_0_73_1.mjs.  Tool execution is
 * strictly sequential in source order, as pinned agent-loop.js:207-276 runs it.
 */
import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import crypto from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
import { pathToFileURL } from "node:url";

const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const SCHEMA_VERSION = "bb.native-worker.rpc.v1";
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
const trackedProcessGroups = new Set();

function processGroupAlive(entry) {
  if (entry.pgid === null) return false;
  try {
    process.kill(-entry.pgid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function signalProcessGroup(entry) {
  if (entry.pgid === null) return false;
  try {
    process.kill(-entry.pgid, "SIGKILL");
    entry.signalSent = true;
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    return false;
  }
}

function markLeaderExit(entry) {
  if (entry.leaderExited) return;
  entry.leaderExited = true;
  entry.groupAliveAtExit = processGroupAlive(entry);
  if (entry.groupAliveAtExit) entry.reportable = true;
}

function trackBashProcess(child) {
  if (!child.pid) return null;
  const entry = {
    child,
    pid: child.pid,
    pgid: process.platform === "win32" ? null : child.pid,
    done: null,
    leaderExited: false,
    groupAliveAtExit: false,
    reportable: false,
    signalSent: false,
  };
  trackedProcessGroups.add(entry);
  child.once("exit", () => markLeaderExit(entry));
  return entry;
}

function maybeForgetProcess(entry) {
  if (entry.leaderExited && !entry.groupAliveAtExit) trackedProcessGroups.delete(entry);
}

function requestProcessGroupTermination(entry) {
  if (entry.leaderExited) {
    if (entry.groupAliveAtExit && !entry.signalSent && processGroupAlive(entry)) {
      signalProcessGroup(entry);
    }
    return;
  }
  entry.reportable = true;
  signalProcessGroup(entry);
}

/**
 * Pinned bash tool operations (createBashTool options.operations,
 * core/tools/bash.js:104-107) with process-group tracking.  The body mirrors
 * pinned defaultBashOperations (bash.js:23-95): same shell resolution order,
 * detached spawn, `close`-based settlement, and rejection messages; the
 * tree kill (killProcessTree -> SIGKILL -pid, utils/shell.js:157-185) goes
 * through the tracked group so close() can prove every group is dead.
 */
function createTrackedBashOperations() {
  return {
    exec(command, execCwd, { onData, signal, timeout, env }) {
      return new Promise((resolvePromise, reject) => {
        const { shell, args } = getShellConfig();
        if (!existsSync(execCwd)) {
          reject(new Error(`Working directory does not exist: ${execCwd}\nCannot execute bash commands.`));
          return;
        }
        const child = spawn(shell, [...args, command], {
          cwd: execCwd,
          detached: true,
          env: env ?? getShellEnv(),
          stdio: ["ignore", "pipe", "pipe"],
        });
        const entry = trackBashProcess(child);
        let settleDone;
        if (entry) entry.done = new Promise((resolveDone) => { settleDone = resolveDone; });
        let timedOut = false;
        let timeoutHandle;
        const onAbort = () => {
          if (entry) requestProcessGroupTermination(entry);
        };
        const cleanup = () => {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          if (signal) signal.removeEventListener("abort", onAbort);
          if (entry) {
            maybeForgetProcess(entry);
            settleDone();
          }
        };
        if (timeout !== undefined && timeout > 0) {
          timeoutHandle = setTimeout(() => {
            timedOut = true;
            if (entry) requestProcessGroupTermination(entry);
          }, timeout * 1000);
        }
        if (child.stdout) child.stdout.on("data", onData);
        if (child.stderr) child.stderr.on("data", onData);
        child.on("error", (error) => {
          cleanup();
          reject(error);
        });
        if (signal) {
          if (signal.aborted) onAbort();
          else signal.addEventListener("abort", onAbort, { once: true });
        }
        child.on("close", (code) => {
          cleanup();
          if (signal?.aborted) {
            reject(new Error("aborted"));
            return;
          }
          if (timedOut) {
            reject(new Error(`timeout:${timeout}`));
            return;
          }
          resolvePromise({ exitCode: code });
        });
      });
    },
  };
}

async function waitForProcessGroupDead(entry) {
  if (!entry.leaderExited) return false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (!processGroupAlive(entry)) return true;
    await new Promise((resolveWait) => setTimeout(resolveWait, 10));
  }
  return !processGroupAlive(entry);
}

async function closeTrackedProcessGroups() {
  const entries = [...trackedProcessGroups];
  for (const entry of entries) requestProcessGroupTermination(entry);
  await Promise.allSettled(entries.map((entry) => entry.done));
  const processes = [];
  for (const entry of entries) {
    if (!entry.reportable) {
      trackedProcessGroups.delete(entry);
      continue;
    }
    const dead = await waitForProcessGroupDead(entry);
    processes.push({ pid: entry.pid, pgid: entry.pgid, dead });
    if (dead) trackedProcessGroups.delete(entry);
  }
  return { processes, all_dead: processes.every(({ dead }) => dead) };
}

let initializedState = null;
let retainedPreparedBatch = null;

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

/**
 * Tool registry as pinned AgentSession._buildRuntime builds it
 * (agent-session.js:1746-1755: createAllTools(cwd, {read: {autoResizeImages},
 * bash: {commandPrefix}}), tools/index.js:48-58), with the bash tool's
 * operations hook tracking process groups.
 */
function createToolRegistry(cwd, settingsManager) {
  const autoResizeImages = settingsManager.getImageAutoResize();
  const commandPrefix = settingsManager.getShellCommandPrefix();
  const tools = {
    read: createReadTool(cwd, { autoResizeImages }),
    bash: createBashTool(cwd, { commandPrefix, operations: createTrackedBashOperations() }),
    edit: createEditTool(cwd),
    write: createWriteTool(cwd),
    grep: createGrepTool(cwd),
    find: createFindTool(cwd),
    ls: createLsTool(cwd),
  };
  return TOOL_ORDER.map((name) => {
    const tool = tools[name];
    if (tool.name !== name) fail(`pinned tool factory ${name} produced ${tool.name}`);
    return tool;
  });
}

async function initialize(payload) {
  requireExactKeys(payload, INITIALIZE_KEYS, "initialize payload");
  const workspace = requiredString(payload, "workspace");
  const scratch = requiredString(payload, "scratch");
  const packageDir = requiredString(payload, "package_dir");
  if (typeof payload.task !== "string") fail("initialize requires task");
  const runtimeInputs = payload.runtime_inputs;
  requireExactKeys(runtimeInputs, RUNTIME_INPUT_KEYS, "runtime_inputs");
  if (Object.values(runtimeInputs).some((value) => typeof value !== "string" || !value)) {
    fail("initialize requires declared runtime_inputs");
  }
  if (
    runtimeInputs.cwd !== workspace
    || runtimeInputs.home !== resolve(scratch, "home")
    || runtimeInputs.package_dir !== packageDir
  ) {
    fail("initialize runtime_inputs do not match worker authority");
  }
  // 0.57.1 advertises the pinned tool descriptions and prompt verbatim.
  requireExactKeys(payload.advertisement, [], "advertisement");
  const model = modelFromConfig(payload.model_config);
  verifyPinnedVersions();
  const agentDir = resolve(scratch, "pi-agent");
  const home = resolve(scratch, "home");
  const tmpdir = resolve(scratch, "tmp");
  await mkdir(agentDir);
  // The attested lease envelope creates <scratch>/home and exports it as HOME.
  await mkdir(home, { recursive: true });
  await mkdir(tmpdir);
  process.env.HOME = home;
  process.env.TMPDIR = tmpdir;
  // The declared runtime provisions neither fd nor rg; pinned find/grep then
  // report their own unavailability (find.js:93-96, grep.js:45-48).
  for (const tool of ["fd", "rg"]) {
    if (getToolPath(tool) !== null) fail(`declared Pi runtime must not resolve ${tool}`);
  }
  // Resource loader exactly as pinned main.js:505-523 builds it for
  // `--no-extensions --no-skills --no-prompt-templates` (no other resource flags).
  const settingsManager = SettingsManager.create(workspace, agentDir);
  const resourceLoader = new DefaultResourceLoader({
    cwd: workspace,
    agentDir,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
  });
  await resourceLoader.reload();
  const loaderSystemPrompt = resourceLoader.getSystemPrompt();
  const loaderAppendSystemPrompt = resourceLoader.getAppendSystemPrompt();
  const loadedSkills = resourceLoader.getSkills().skills;
  const loadedExtensions = resourceLoader.getExtensions().extensions;
  const contextFiles = resourceLoader.getAgentsFiles().agentsFiles;
  if (loaderSystemPrompt !== undefined || loaderAppendSystemPrompt.length !== 0) {
    fail("declared Pi runtime must not discover SYSTEM.md or APPEND_SYSTEM.md");
  }
  if (loadedSkills.length !== 0 || loadedExtensions.length !== 0) {
    fail("declared Pi runtime must not load skills or extensions");
  }
  // sdk.js:130-135 passes converted messages through unchanged unless
  // images.blockImages is set; the declared runtime never sets it.
  if (settingsManager.getBlockImages()) fail("declared Pi runtime must not block images");
  const tools = createToolRegistry(workspace, settingsManager);
  const schemas = tools.map((tool) => ({
    type: "function",
    function: { name: tool.name, description: tool.description, parameters: tool.parameters },
  }));
  const oldTz = process.env.TZ;
  const oldPackageDir = process.env.PI_PACKAGE_DIR;
  process.env.TZ = "UTC";
  process.env.PI_PACKAGE_DIR = packageDir;
  let systemPrompt;
  try {
    // agent-session.js:536-564.  toolSnippets/promptGuidelines only carry
    // extension and SDK custom tools (agent-session.js:1698-1714); this
    // runtime has none, so both stay empty and the built-in descriptions apply.
    systemPrompt = buildSystemPrompt({
      cwd: workspace,
      skills: loadedSkills,
      contextFiles,
      customPrompt: loaderSystemPrompt,
      appendSystemPrompt: undefined,
      selectedTools: [...TOOL_ORDER],
      toolSnippets: {},
      promptGuidelines: [],
    });
  } finally {
    if (oldTz === undefined) delete process.env.TZ;
    else process.env.TZ = oldTz;
    if (oldPackageDir === undefined) delete process.env.PI_PACKAGE_DIR;
    else process.env.PI_PACKAGE_DIR = oldPackageDir;
  }
  // system-prompt.js:155-156 renders these two lines last.
  const rendered = /\nCurrent date and time: ([^\n]*)\nCurrent working directory: ([^\n]*)$/.exec(systemPrompt);
  if (!rendered || rendered[2] !== workspace) fail("rendered Pi prompt lacks the pinned date/time and cwd lines");
  initializedState = {
    workspace,
    systemPrompt,
    model,
    tools,
    toolsByName: new Map(tools.map((tool) => [tool.name, tool])),
    bootstrap: {
      task: payload.task,
      model_config: payload.model_config,
      cwd: workspace,
      home,
      current_date_time: rendered[1],
      project_context: contextFiles,
      package_dir: packageDir,
      advertisement: payload.advertisement,
    },
  };
  retainedPreparedBatch = null;
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: schemas,
    bootstrap: initializedState.bootstrap,
  };
}

/**
 * Pinned LLM context for one agent-loop turn: agent-loop.js:137-146 runs
 * transformContext (no extension runner -> identity, sdk.js:176-181) and then
 * convertToLlm (core/messages.js:75-122 via sdk.js:130-135).
 */
function llmContext(messages) {
  if (!Array.isArray(messages)) fail("messages must be a list");
  for (const message of messages) {
    if (!isPlainObject(message) || !["user", "assistant", "toolResult", "compactionSummary"].includes(message.role)) {
      fail("messages must be pinned AgentMessages");
    }
  }
  return {
    systemPrompt: initializedState.systemPrompt,
    messages: convertToLlm(messages),
    tools: initializedState.tools,
  };
}

/**
 * Pinned stream options for `--thinking off`: agent.js:286 sets reasoning
 * undefined for thinking level "off" and agent-loop.js:151-155 passes
 * {...config, apiKey, signal}.  Of those config fields openai-completions only
 * reads apiKey, signal, onPayload and reasoning (openai-completions.js:254-266,
 * simple-options.js:1-14); maxTokens is not passed, so pinned buildBaseOptions
 * derives Math.min(model.maxTokens, 32000) (simple-options.js:4).
 */
async function runPinnedStream(messages, onPayload, signal) {
  const stream = streamSimpleOpenAICompletions(initializedState.model, llmContext(messages), {
    reasoning: undefined,
    apiKey: PROVIDER_API_KEY,
    signal,
    onPayload,
  });
  for await (const _event of stream) {
    // Drain the pinned event stream; its terminal message is the result.
  }
  return stream.result();
}

async function projectRequest(payload) {
  if (!initializedState) fail("project_request requires initialize");
  requireExactKeys(payload, ["messages"], "project_request payload");
  const sentinel = new Error(`bb-pi-0.57.1-request-projection-${randomBytes(16).toString("hex")}`);
  let captured = null;
  // openai-completions.js:51-56 builds params and awaits onPayload before
  // client.chat.completions.create, so throwing here performs no I/O and the
  // pinned catch (:239-249) turns it into the terminal error message.
  const message = await runPinnedStream(payload.messages, (params) => {
    captured = params;
    throw sentinel;
  }, new AbortController().signal);
  if (captured === null || message.stopReason !== "error" || message.errorMessage !== sentinel.message) {
    fail("pinned request projection did not stop at the payload sentinel");
  }
  // openai-completions.js:604-615 (convertTools) appends `strict: false` to
  // each function under compat.supportsStrictMode; that member is a request
  // builder concern the caller profile re-emits (strict_tools), so the source
  // tool surface carries the pinned schemas without it and request_body keeps
  // the exact pinned body for the Conductor's equality check.
  const tools = captured.tools.map((tool, index) => {
    const { strict, ...fn } = tool.function;
    const reference = initializedState.tools[index];
    if (
      strict !== false || Object.keys(tool).length !== 2 || reference === undefined
      || fn.name !== reference.name || fn.description !== reference.description || fn.parameters !== reference.parameters
    ) {
      fail("pinned request tools differ from the initialized tool registry");
    }
    return { type: tool.type, function: fn };
  });
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "request",
    messages: captured.messages,
    tools,
    request_members: Object.keys(captured),
    request_body: captured,
  };
}

async function projectProviderFailure(payload) {
  if (!initializedState) fail("project_provider_failure requires initialize");
  requireExactKeys(payload, ["http_status", "response_body_text", "messages"], "project_provider_failure payload");
  const status = payload.http_status;
  const errText = payload.response_body_text;
  if (!Number.isInteger(status) || status < 100 || status > 599) fail("http_status must be an HTTP status code");
  if (typeof errText !== "string") fail("response_body_text must be a string");
  await syncSessionMessages(payload.messages, initializedState.model);
  // openai@6.26.0 client.mjs:351-353,362 (makeRequest) and :193-195
  // (makeStatusError): safeJSON(errText), message only when the body is not
  // JSON, then APIError.generate(status, errJSON, errMessage, response.headers).
  const errJSON = safeJSON(errText);
  const errMessage = errJSON ? undefined : errText;
  const error = APIError.generate(status, errJSON, errMessage, new Headers());
  // The SDK error surfaces from client.chat.completions.create
  // (openai-completions.js:56); throwing it from onPayload (:52) reaches the
  // same pinned catch (:239-249), which builds the terminal assistant message.
  const message = await withReplayClock(nextTimestamp(), () => runPinnedStream(payload.messages, () => {
    throw error;
  }, new AbortController().signal));
  if (message.stopReason !== "error") fail("pinned provider failure did not produce an error message");
  appendSessionEntry({ type: "message", message }, message.timestamp);
  observedMessages.push(structuredClone(message));
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "provider_failure", message };
}

function parseStreamingJsonBatch(payload) {
  requireExactKeys(payload, ["inputs"], "parse_streaming_json_batch payload");
  if (!Array.isArray(payload.inputs)) fail("parse_streaming_json_batch requires inputs");
  const results = payload.inputs.map((input) => {
    if (input !== null && typeof input !== "string") fail("streaming JSON input must be a string or null");
    // Pinned parseStreamingJson owns every fallback, including {} for empty input.
    return parseStreamingJson(input ?? undefined);
  });
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "parsed_streaming_json_batch", results };
}

function errorText(error) {
  // agent-loop.js:237-240
  return error instanceof Error ? error.message : String(error);
}

function prepareTools(payload) {
  if (!initializedState) fail("prepare_tools requires initialize");
  requireExactKeys(payload, ["calls"], "prepare_tools payload");
  if (!Array.isArray(payload.calls)) fail("prepare_tools requires calls");
  const prepared = [];
  const calls = [];
  const errors = [];
  for (const call of payload.calls) {
    requireExactKeys(call, ["id", "name", "arguments"], "tool call");
    if (typeof call.id !== "string" || !call.id || typeof call.name !== "string") {
      fail("tool call id and name must be strings");
    }
    const toolCall = { type: "toolCall", id: call.id, name: call.name, arguments: call.arguments };
    try {
      // agent-loop.js:213 lookup and :223-225 not-found/validation, in order.
      const tool = initializedState.tools.find((candidate) => candidate.name === toolCall.name);
      if (!tool) throw new Error(`Tool ${toolCall.name} not found`);
      const argumentsValue = validateToolArguments(tool, toolCall);
      prepared.push({ id: call.id, tool, argumentsValue });
      calls.push({ id: call.id, name: call.name, arguments: argumentsValue });
    } catch (error) {
      const message = errorText(error);
      errors.push(message);
      prepared.push({ id: call.id, error: message });
      calls.push({ id: call.id, name: call.name, arguments: call.arguments, error: message });
    }
  }
  retainedPreparedBatch = prepared;
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "prepared",
    calls,
    ...(errors.length ? { errors } : {}),
  };
}

async function executeBatch(payload, signal) {
  requireExactKeys(payload, [], "execute_batch payload");
  if (!retainedPreparedBatch) fail("execute_batch requires the retained prepared batch");
  const prepared = retainedPreparedBatch;
  retainedPreparedBatch = null;
  const results = [];
  // agent-loop.js:207-276: one call at a time, in source order.
  for (const [index, call] of prepared.entries()) {
    let result;
    let isError = false;
    if (call.error !== undefined) {
      result = { content: [{ type: "text", text: call.error }], details: {} };
      isError = true;
    } else {
      try {
        // agent-loop.js:226-234 passes an update callback; updates are not
        // model-visible, so they are dropped here.
        result = await call.tool.execute(call.id, call.argumentsValue, signal, () => {});
      } catch (error) {
        result = { content: [{ type: "text", text: errorText(error) }], details: {} };
        isError = true;
      }
    }
    // agent-loop.js:250-258 copies result.details (:255); an undefined value is
    // absent from the serialized toolResult.
    results.push({
      id: call.id,
      completion_index: index,
      content: result.content,
      ...(result.details !== undefined ? { details: result.details } : {}),
      isError,
      terminate: false,
    });
  }
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "tool_results", results };
}

async function close(payload) {
  requireExactKeys(payload, [], "close payload");
  retainedPreparedBatch = null;
  const cleanup = await closeTrackedProcessGroups();
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "closed", cleanup };
}
// Episode-local stock SessionManager, retained by the serial framed worker.
// Id seam: session-manager.js 0.73.1:13-20; 0.57.1:9-16.
// Entry clock seam: 0.73.1:580-589,617-630; 0.57.1:574-583,611-624.
const sessionManager = sessionModule.SessionManager.inMemory();
const sessionEntries = [];
let observedMessages = [];
let replayClock = 1000;
let overflowRecoveryAttempted = false;
const NativeDate = Date;
function nextTimestamp() { return ++replayClock; }
async function withReplayClock(timestamp, action) {
  globalThis.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [timestamp])); }
    static now() { return timestamp; }
  };
  try { return await action(); }
  finally { globalThis.Date = NativeDate; }
}
function appendSessionEntry(fields, timestamp) {
  const originalUuid = crypto.randomUUID;
  const originalDate = globalThis.Date;
  crypto.randomUUID = () => `${sessionEntries.length.toString(16).padStart(8, "0")}-0000-0000-0000-000000000000`;
  syncBuiltinESMExports();
  globalThis.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [timestamp])); }
    static now() { return timestamp; }
  };
  try {
    const id = fields.type === "message"
      ? sessionManager.appendMessage(fields.message)
      : sessionManager.appendCompaction(fields.summary, fields.firstKeptEntryId,
          fields.tokensBefore, fields.details, fields.fromHook);
    const entry = sessionManager.getEntry(id);
    sessionEntries.push(entry);
    return entry;
  } finally {
    crypto.randomUUID = originalUuid;
    syncBuiltinESMExports();
    globalThis.Date = originalDate;
  }
}
async function syncSessionMessages(messages, model) {
  if (messages.length < observedMessages.length
      || observedMessages.some((m, i) => JSON.stringify(m) !== JSON.stringify(messages[i]))) {
    fail("conductor history differs from retained Pi session context");
  }
  for (const original of messages.slice(observedMessages.length)) {
    if (!["user", "assistant", "toolResult"].includes(original.role)) {
      fail("new session messages must be stock AgentMessages");
    }
    // Clock seams: agent-session.js:656 (user), pi-ai openai-completions.js:46
    // (assistant), pi-agent-core agent-loop.js:257 (toolResult).
    // Replay event order, not BB's query-start timestamp across retries.
    const timestamp = nextTimestamp();
    const message = { ...original, timestamp };
    // Stock lifecycle reset: agent-session.js:186,223-227.
    if (message.role === "user" || (message.role === "assistant" && message.stopReason !== "error")) {
      overflowRecoveryAttempted = false;
    }
    // Stock initializes usage even when the provider emits no usage chunk
    // (0.57.1 openai-completions.js:31-47; 0.73.1:54-69).
    if (message.role === "assistant" && message.usage === undefined) {
      message.usage = await parseProviderUsage(undefined, timestamp, model);
    }
    appendSessionEntry({ type: "message", message }, timestamp);
  }
  observedMessages = structuredClone(messages);
}
// Replay only the provider transport, running the stock OpenAI stream parser.
// Usage parsing: 0.73.1 openai-completions.js:199-200; 0.57.1:92-115.
// Assistant clock: 0.73.1:69; 0.57.1:46.
async function parseProviderUsage(usage, timestamp, model) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    `data: ${JSON.stringify({ choices: [], usage })}\n\ndata: [DONE]\n\n`,
    { headers: { "content-type": "text/event-stream" } },
  );
  try {
    const message = await withReplayClock(timestamp, async () => {
      const stream = openaiCompletions.streamSimpleOpenAICompletions(model,
        { messages: [] }, { apiKey: "EMPTY" });
      for await (const event of stream) { /* stock parser owns usage */ }
      return stream.result();
    });
    if (message.stopReason === "error") fail(message.errorMessage);
    return message.usage;
  } finally { globalThis.fetch = originalFetch; }
}
async function prepareSession(payload, model) {
  await syncSessionMessages(payload.messages, model);
  const latest = sessionEntries.findLast((e) => e.type === "message"
    && e.message.role === "assistant" && e.message.stopReason !== "error");
  // The phase protocol's usage is the provider's raw OpenAI usage, not a Pi
  // alias. Parsing it also restores the last successful response before overflow.
  if (latest && ((payload.usage !== undefined && payload.usage !== null)
      || (latest.message.usage === undefined
        && (payload.reason === "threshold" || payload.checkpoint === "agent_end")))) {
    latest.message.usage = await parseProviderUsage(payload.usage, latest.message.timestamp, model);
  }
  return sessionManager.getBranch();
}
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
    return overflowRecoveryAttempted ? null : "overflow";
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

async function prepareCompactionPhase(payload) {
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
    if (overflowRecoveryAttempted) {
      return { schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_unavailable", reason: "overflow_retry_exhausted" };
    }
    overflowRecoveryAttempted = true;
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

async function finalizeCompactionPhase(payload) {
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
  observedMessages = structuredClone(messages);
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
    await withReplayClock(replayClock, () => agent.continue().catch(() => {}));
  }
  return {
    schema_version: PHASE_SCHEMA_VERSION, kind: "compaction_finalized", messages,
    summary: result.summary, reason: prep.reason, retry,
  };
}


async function executeOperation(operation, payload, signal) {
  if (operation === "initialize") return initialize(payload);
  if (operation === "project_request") return projectRequest(payload);
  if (operation === "parse_provider_usage") {
    const usage = await parseProviderUsage(payload.usage, replayClock, initializedState.model);
    return { schema_version: PHASE_SCHEMA_VERSION, kind: "assistant_usage", usage };
  }
  if (operation === "parse_streaming_json_batch") return parseStreamingJsonBatch(payload);
  if (operation === "prepare_tools") return prepareTools(payload);
  if (operation === "execute_batch") return executeBatch(payload, signal);
  if (operation === "project_provider_failure") return projectProviderFailure(payload);
  if (operation === "prepare_compaction") return prepareCompactionPhase(payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(payload);
  if (operation === "close") return close(payload);
  fail(`unknown native operation: ${operation}`);
}

let frameIterator;
let frameBuffer = Buffer.alloc(0);
async function readFrame() {
  if (!frameIterator) frameIterator = process.stdin[Symbol.asyncIterator]();
  while (frameBuffer.length < 4) {
    const next = await frameIterator.next();
    if (next.done) return null;
    frameBuffer = Buffer.concat([frameBuffer, Buffer.from(next.value)]);
  }
  const size = frameBuffer.readUInt32BE(0);
  if (size === 0 || size > MAX_FRAME_BYTES) fail("native frame length is invalid");
  while (frameBuffer.length < size + 4) {
    const next = await frameIterator.next();
    if (next.done) fail("native frame ended early");
    frameBuffer = Buffer.concat([frameBuffer, Buffer.from(next.value)]);
  }
  const body = frameBuffer.subarray(4, size + 4);
  frameBuffer = frameBuffer.subarray(size + 4);
  return JSON.parse(body.toString("utf8"));
}

function writeFrame(value) {
  const body = Buffer.from(JSON.stringify(value), "utf8");
  if (body.length > MAX_FRAME_BYTES) fail("native response exceeds frame limit");
  process.stdout.write(Buffer.concat([Buffer.from([(body.length >>> 24) & 0xff, (body.length >>> 16) & 0xff, (body.length >>> 8) & 0xff, body.length & 0xff]), body]));
}

async function mainFramed() {
  const controller = new AbortController();
  const abort = () => controller.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);
  try {
    while (true) {
      const command = await readFrame();
      if (command === null) return;
      const requestId = command?.request_id;
      try {
        if (command?.schema_version !== SCHEMA_VERSION || !Number.isInteger(requestId) || requestId <= 0 || typeof command.operation !== "string" || !command.payload || typeof command.payload !== "object") {
          fail("native command authority is invalid");
        }
        const result = await executeOperation(command.operation, command.payload, controller.signal);
        writeFrame({ schema_version: SCHEMA_VERSION, request_id: requestId, result });
      } catch (error) {
        writeFrame({ schema_version: SCHEMA_VERSION, request_id: requestId, error: { type: error?.constructor?.name ?? "Error", message: String(error?.message ?? error).slice(0, 4096) } });
      }
    }
  } finally {
    process.removeListener("SIGINT", abort);
    process.removeListener("SIGTERM", abort);
  }
}

mainFramed().catch((error) => {
  console.error(`pi-tools-0.57.1 protocol failure: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
