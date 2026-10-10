#!/usr/bin/env node
/**
 * Pinned Pi 0.73.1 native tool worker.
 *
 * One-shot stdin accepts one call or {calls:[...]}; set
 * PI_NATIVE_WORKER_FRAMED=1 for the persistent bb.native-worker.rpc.v1
 * length-prefixed protocol.  In either mode tool preparation and execution
 * run in the pinned Node process, and batch calls share Pi's mutation queue.
 */
import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import crypto from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const SCHEMA_VERSION = "bb.native-worker.rpc.v1";
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

function createTrackedBashTool(cwd) {
  const operations = {
    exec(command, execCwd, { onData, signal, timeout, env }) {
      return new Promise((resolve, reject) => {
        if (!existsSync(execCwd)) {
          reject(new Error(`Working directory does not exist: ${execCwd}\nCannot execute bash commands.`));
          return;
        }
        const { shell, args } = getShellConfig();
        const child = spawn(shell, [...args, command], {
          cwd: execCwd,
          detached: process.platform !== "win32",
          env: env ?? getShellEnv(),
          stdio: ["ignore", "pipe", "pipe"],
        });
        const entry = trackBashProcess(child);
        let timedOut = false;
        let timeoutHandle;
        const onAbort = () => {
          if (entry) requestProcessGroupTermination(entry);
        };
        const cleanup = () => {
          clearTimeout(timeoutHandle);
          if (signal) signal.removeEventListener("abort", onAbort);
          if (entry) maybeForgetProcess(entry);
        };
        if (entry && timeout !== undefined && timeout > 0) {
          timeoutHandle = setTimeout(() => {
            timedOut = true;
            requestProcessGroupTermination(entry);
          }, timeout * 1000);
        }
        child.stdout?.on("data", onData);
        child.stderr?.on("data", onData);
        if (signal) {
          if (signal.aborted) onAbort();
          else signal.addEventListener("abort", onAbort, { once: true });
        }
        const done = waitForChildProcess(child)
          .then((code) => {
            cleanup();
            if (signal?.aborted) {
              reject(new Error("aborted"));
              return;
            }
            if (timedOut) {
              reject(new Error(`timeout:${timeout}`));
              return;
            }
            resolve({ exitCode: code });
          })
          .catch((error) => {
            cleanup();
            reject(error);
          });
        if (entry) entry.done = done;
      });
    },
  };
  return createBashTool(cwd, { operations });
}

async function waitForProcessGroupDead(entry) {
  if (!entry.leaderExited) return false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (!processGroupAlive(entry)) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
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

const TOOL_FACTORIES = Object.freeze({
  bash: (cwd) => createTrackedBashTool(cwd),
  edit: (cwd) => createEditTool(cwd),
  read: (cwd) => createReadTool(cwd, { autoResizeImages: true }),
  write: (cwd) => createWriteTool(cwd),
});
const TOOL_DEFINITION_FACTORIES = Object.freeze({
  bash: (cwd) => createBashToolDefinition(cwd),
  edit: (cwd) => createEditToolDefinition(cwd),
  read: (cwd) => createReadToolDefinition(cwd, { autoResizeImages: true }),
  write: (cwd) => createWriteToolDefinition(cwd),
});
let initializedState = null;
let retainedPreparedBatch = null;
let nextBatchId = 1;
function fail(message) {
  throw new Error(message);
}

async function readRequest() {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.byteLength;
    if (bytes > MAX_REQUEST_BYTES) fail(`request exceeds ${MAX_REQUEST_BYTES} bytes`);
    chunks.push(value);
  }
  if (bytes === 0) fail("request stdin is empty");
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    fail(`request is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function validateCall(call, defaultCwd) {
  if (call === null || typeof call !== "object" || Array.isArray(call)) fail("call must be an object");
  const toolId = call.tool_id ?? call.toolId ?? call.name;
  if (typeof toolId !== "string" || !TOOL_IDS.has(toolId)) fail(`unknown tool_id: ${String(toolId)}`);
  const argumentsValue = call.arguments;
  const cwd = typeof call.cwd === "string" && call.cwd ? call.cwd : defaultCwd;
  const callIdValue = call.call_id ?? call.callId ?? call.id;
  const callId = typeof callIdValue === "string" && callIdValue ? callIdValue : "bb-native-call";
  return { toolId, argumentsValue, cwd, callId };
}

function prepareCall(call, defaultCwd) {
  const request = validateCall(call, defaultCwd);
  const tool = TOOL_FACTORIES[request.toolId](request.cwd);
  const prepared = typeof tool.prepareArguments === "function"
    ? tool.prepareArguments(request.argumentsValue)
    : request.argumentsValue;
  const argumentsValue = validateToolArguments(tool, {
    id: request.callId,
    name: request.toolId,
    arguments: prepared,
  });
  return { request, tool, argumentsValue };
}


async function executePrepared(prepared, signal) {
  try {
    const result = await prepared.tool.execute(prepared.request.callId, prepared.argumentsValue, signal);
    const details = result?.details && typeof result.details === "object" ? {...result.details} : {};
    delete details.effects;
    return {
      content: Array.isArray(result?.content) ? result.content : [],
      details,
      isError: false,
      terminate: false,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
      terminate: false,
    };
  }
}

async function executeCall(call, defaultCwd, signal) {
  try {
    return await executePrepared(prepareCall(call, defaultCwd), signal);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
      terminate: false,
    };
  }
}

function requiredString(payload, key) {
  const value = payload?.[key];
  if (typeof value !== "string" || !value) fail(`initialize requires ${key}`);
  return value;
}

function toolSchemas(workspace, advertisement) {
  return [...TOOL_IDS].map((name) => {
    const tool = TOOL_FACTORIES[name](workspace);
    const replacement = advertisement?.tools?.[name]?.description;
    const description = typeof replacement === "string" ? replacement : tool.description;
    return {
      type: "function",
      function: { name: tool.name, description, parameters: tool.parameters },
    };
  });
}

function originalDescriptionHashes(workspace) {
  return Object.fromEntries([...TOOL_IDS].map((name) => {
    const description = TOOL_FACTORIES[name](workspace).description ?? "";
    return [name, createHash("sha256").update(description, "utf8").digest("hex")];
  }));
}
async function initialize(payload) {
  const workspace = requiredString(payload, "workspace");
  const scratch = requiredString(payload, "scratch");
  const packageDir = requiredString(payload, "package_dir");
  const runtimeInputs = payload.runtime_inputs;
  if (
    !runtimeInputs
    || typeof runtimeInputs !== "object"
    || Array.isArray(runtimeInputs)
    || Object.keys(runtimeInputs).length !== 4
    || Object.keys(runtimeInputs).some((key) => !["cwd", "home", "current_date", "package_dir"].includes(key))
    || Object.values(runtimeInputs).some((value) => typeof value !== "string" || !value)
  ) {
    fail("initialize requires declared runtime_inputs");
  }
  if (
    runtimeInputs.cwd !== workspace
    || runtimeInputs.home !== resolve(scratch, "home")
    || runtimeInputs.package_dir !== packageDir
  ) {
    fail("initialize runtime_inputs do not match worker authority");
  }
  const advertisement = payload.advertisement;
  if (!advertisement || typeof advertisement !== "object" || Array.isArray(advertisement)) {
    fail("initialize requires advertisement");
  }
  const advertisementKeys = Object.keys(advertisement);
  if (advertisementKeys.length !== 2 || !advertisementKeys.includes("tools") || !advertisementKeys.includes("prompt")) {
    fail("advertisement keys must be exactly tools and prompt");
  }
  const advertisementTools = advertisement.tools;
  if (!advertisementTools || typeof advertisementTools !== "object" || Array.isArray(advertisementTools)) {
    fail("advertisement.tools must contain exactly read");
  }
  const advertisedToolNames = Object.keys(advertisementTools);
  if (advertisedToolNames.length !== 1 || advertisedToolNames[0] !== "read") {
    fail("advertisement.tools must contain exactly read");
  }
  const readAdvertisement = advertisementTools.read;
  if (!readAdvertisement || typeof readAdvertisement !== "object" || Array.isArray(readAdvertisement)) {
    fail("advertisement.tools.read keys must be exactly description and native_sha256");
  }
  const readAdvertisementKeys = Object.keys(readAdvertisement);
  if (
    readAdvertisementKeys.length !== 2
    || !readAdvertisementKeys.includes("description")
    || !readAdvertisementKeys.includes("native_sha256")
  ) {
    fail("advertisement.tools.read keys must be exactly description and native_sha256");
  }
  if (typeof readAdvertisement.description !== "string" || typeof readAdvertisement.native_sha256 !== "string") {
    fail("advertisement.tools.read must provide description and native_sha256");
  }
  if (!/^sha256:[0-9a-f]{64}$/.test(readAdvertisement.native_sha256)) {
    fail("advertisement.tools.read native_sha256 must be sha256:<64 lowercase hex>");
  }
  const advertisementPrompt = advertisement.prompt;
  if (!advertisementPrompt || typeof advertisementPrompt !== "object" || Array.isArray(advertisementPrompt)) {
    fail("advertisement.prompt keys must be exactly remove_exact");
  }
  const promptKeys = Object.keys(advertisementPrompt);
  if (promptKeys.length !== 1 || promptKeys[0] !== "remove_exact") {
    fail("advertisement.prompt keys must be exactly remove_exact");
  }
  const removeExact = advertisementPrompt.remove_exact;
  if (!Array.isArray(removeExact) || removeExact.length === 0 || removeExact.some((value) => typeof value !== "string" || !value)) {
    fail("advertisement.prompt.remove_exact must be a non-empty string list");
  }
  const modelConfig = payload.model_config;
  if (!modelConfig || typeof modelConfig !== "object" || Array.isArray(modelConfig)) {
    fail("initialize requires model_config");
  }
  const nativeDescriptionSha256 = originalDescriptionHashes(workspace);
  if (readAdvertisement.native_sha256 !== `sha256:${nativeDescriptionSha256.read}`) {
    fail("advertisement native description hash mismatch for read");
  }
  const agentDir = resolve(scratch, "pi-agent");
  const home = resolve(scratch, "home");
  const tmpdir = resolve(scratch, "tmp");
  await mkdir(agentDir);
  // The attested lease envelope creates <scratch>/home and exports it as HOME.
  await mkdir(home, { recursive: true });
  await mkdir(tmpdir);
  process.env.HOME = home;
  process.env.TMPDIR = tmpdir;
  const projectContext = loadProjectContextFiles({ cwd: workspace, agentDir });
  const schemas = toolSchemas(workspace, advertisement);
  const snippets = Object.fromEntries([...TOOL_IDS].map((name) => {
    const tool = TOOL_DEFINITION_FACTORIES[name](workspace);
    return [name, tool.promptSnippet ?? tool.description ?? ""];
  }));
  const promptGuidelines = [...TOOL_IDS].flatMap((name) => {
    const tool = TOOL_DEFINITION_FACTORIES[name](workspace);
    return Array.isArray(tool.promptGuidelines) ? tool.promptGuidelines : [];
  });
  const oldTz = process.env.TZ;
  const oldPackageDir = process.env.PI_PACKAGE_DIR;
  process.env.TZ = "UTC";
  process.env.PI_PACKAGE_DIR = packageDir;
  let systemPrompt;
  let currentDate;
  try {
    currentDate = runtimeInputs.current_date;
    systemPrompt = buildSystemPrompt({
      cwd: workspace,
      contextFiles: projectContext,
      selectedTools: [...TOOL_IDS],
      toolSnippets: snippets,
      promptGuidelines,
    });
    // Pinned supplier buildSystemPrompt reads the wall clock itself; fail closed
    // if it rendered a date other than the declared runtime input (e.g. a UTC
    // midnight crossing between declaration and initialize).
    const renderedDate = /\nCurrent date: ([^\n]*)\nCurrent working directory: [^\n]*$/.exec(systemPrompt);
    if (!renderedDate || renderedDate[1] !== currentDate) {
      fail("rendered Pi prompt current date does not match declared runtime_inputs.current_date");
    }
    const seenRemovals = new Set();
    for (const removal of removeExact) {
      if (seenRemovals.has(removal)) {
        fail("advertisement prompt removal is duplicate");
      }
      if (systemPrompt.split(removal).length !== 2) {
        fail("advertisement prompt removal must occur exactly once");
      }
      seenRemovals.add(removal);
      systemPrompt = systemPrompt.replace(removal, "");
    }
  } finally {
    if (oldTz === undefined) delete process.env.TZ;
    else process.env.TZ = oldTz;
    if (oldPackageDir === undefined) delete process.env.PI_PACKAGE_DIR;
    else process.env.PI_PACKAGE_DIR = oldPackageDir;
  }
  initializedState = {
    workspace,
    scratch,
    home,
    systemPrompt,
    schemas,
    modelConfig,
    bootstrap: {
      task: payload.task ?? "",
      model_config: modelConfig,
      cwd: workspace,
      home,
      current_date: currentDate,
      project_context: projectContext,
      package_dir: packageDir,
      native_description_sha256: Object.fromEntries(
        Object.entries(nativeDescriptionSha256).map(([name, hash]) => [name, `sha256:${hash}`]),
      ),
      advertisement,
    },
  };
  retainedPreparedBatch = null;
  return {
    schema_version: "bb.pi-native.v1",
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: schemas,
    bootstrap: initializedState.bootstrap,
  };
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

function projectRequest(payload) {
  if (!initializedState) fail("project_request requires initialize");
  if (!Array.isArray(payload?.messages)) fail("project_request requires messages");
  const model = modelForProject(initializedState.modelConfig);
  const acceptsImage = model.input.includes("image");
  const messages = convertToLlm(payload.messages).map((message) => {
    if (acceptsImage || !Array.isArray(message?.content)) return message;
    return {
      ...message,
      content: message.content.filter((part) => part?.type !== "image"),
    };
  });
  const context = { systemPrompt: initializedState.systemPrompt, messages };
  const outboundMessages = convertMessages(model, context, model.compat);
  const tools = initializedState.schemas;
  return { schema_version: "bb.pi-native.v1", kind: "request", messages: outboundMessages, tools };
}

async function projectProviderFailure(payload) {
  if (!initializedState) fail("project_provider_failure requires initialize");
  if (!Number.isInteger(payload.http_status) || typeof payload.response_body_text !== "string") {
    fail("project_provider_failure requires HTTP status and response body");
  }
  await syncSessionMessages(payload.messages, modelForProject(initializedState.modelConfig));
  const errJSON = safeJSON(payload.response_body_text);
  // Stock SDK makeStatusError constructs this APIError; the stock pi-ai catch
  // builds the complete error AssistantMessage (openai-completions.js:309-323).
  const error = APIError.generate(payload.http_status, errJSON,
    errJSON ? undefined : payload.response_body_text, new Headers());
  const message = await withReplayClock(nextTimestamp(), async () => {
    const stream = openaiCompletions.streamSimpleOpenAICompletions(
      modelForProject(initializedState.modelConfig),
      { systemPrompt: initializedState.systemPrompt, messages: convertToLlm(payload.messages) },
      { apiKey: "EMPTY", onPayload: () => { throw error; } },
    );
    for await (const event of stream) { /* drain stock terminal error */ }
    return stream.result();
  });
  if (message.stopReason !== "error") fail("stock provider failure did not produce an error");
  appendSessionEntry({ type: "message", message }, message.timestamp);
  observedMessages.push(structuredClone(message));
  return { schema_version: "bb.pi-native.v1", kind: "provider_failure", message };
}

class CompactionPrepareSentinel extends Error {
  constructor() {
    super("CompactionPrepareSentinel");
    this.name = "CompactionPrepareSentinel";
  }
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
    // Clock seams: agent-session.js:769 (user), pi-ai openai-completions.js:69
    // (assistant), pi-agent-core agent-loop.js:451 (toolResult).
    // Replay event order, not BB's query-start timestamp across retries.
    const timestamp = nextTimestamp();
    const message = { ...original, timestamp };
    // Stock lifecycle reset: agent-session.js:274,313-317.
    if (message.role === "user" || (message.role === "assistant" && message.stopReason !== "error")) {
      overflowRecoveryAttempted = false;
    }
    // Stock initializes usage even when the provider emits no usage chunk
    // (0.73.1 openai-completions.js:54-69; 0.57.1:31-47).
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

async function prepareCompactionPhase(payload) {
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
    if (overflowRecoveryAttempted) {
      return { schema_version: "bb.pi-native.v1", kind: "compaction_unavailable", reason: "overflow_retry_exhausted" };
    }
    overflowRecoveryAttempted = true;
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
  observedMessages = structuredClone(messages);
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
    await withReplayClock(replayClock, () => agent.continue().catch(() => {}));
  }
  return {
    schema_version: "bb.pi-native.v1", kind: "compaction_finalized", messages,
    summary: result.summary, reason: prep.reason, retry,
  };
}
async function executeOperation(operation, payload, signal) {
  const defaultCwd = initializedState?.workspace
    ?? (typeof payload?.cwd === "string" && payload.cwd ? payload.cwd : process.cwd());
  if (operation === "initialize") return initialize(payload);
  if (operation === "bootstrap") {
    if (!initializedState) return initialize(payload);
    return {
      schema_version: "bb.pi-native.v1",
      kind: "initialized",
      system_prompt: initializedState.systemPrompt,
      tool_schemas: initializedState.schemas,
      bootstrap: initializedState.bootstrap,
    };
  }
  if (operation === "project_request") return projectRequest(payload);
  if (operation === "parse_provider_usage") {
    const usage = await parseProviderUsage(payload.usage, replayClock, modelForProject(initializedState.modelConfig));
    return { schema_version: "bb.pi-native.v1", kind: "assistant_usage", usage };
  }
  if (operation === "prepare_compaction") return prepareCompactionPhase(payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(payload);
  if (operation === "project_provider_failure") return projectProviderFailure(payload);
  if (operation === "parse_streaming_json_batch") {
    if (!Array.isArray(payload?.inputs)) fail("parse_streaming_json_batch requires inputs");
    const results = payload.inputs.map((input) => {
      if (input !== null && typeof input !== "string") fail("streaming JSON input must be a string or null");
      // Pinned parseStreamingJson owns every fallback, including {} for empty input.
      return parseStreamingJson(input ?? undefined);
    });
    return { schema_version: "bb.pi-native.v1", kind: "parsed_streaming_json_batch", results };
  }
  if (operation === "prepare_tools") {
    if (!Array.isArray(payload?.calls)) fail("prepare_tools requires calls");
    const preparedInternal = [];
    const calls = [];
    const historyCalls = [];
    const errors = [];
    for (const call of payload.calls) {
      try {
        const value = prepareCall(call, defaultCwd);
        const item = {
          id: value.request.callId,
          name: value.request.toolId,
          arguments: value.argumentsValue,
        };
        preparedInternal.push({ ...item });
        calls.push({ ...item });
        // Pi keeps the sampled call in assistant history; only execution uses
        // the validator's converted argument clone.
        historyCalls.push({
          id: value.request.callId,
          name: value.request.toolId,
          arguments: value.request.argumentsValue,
        });
      } catch (error) {
        const message = String(error?.message ?? error);
        const item = {
          id: String(call?.call_id ?? call?.callId ?? call?.id ?? ""),
          name: String(call?.tool_id ?? call?.toolId ?? call?.name ?? ""),
          arguments: call?.arguments !== undefined ? call.arguments : {},
          error: message,
        };
        errors.push(message);
        preparedInternal.push({ ...item });
        calls.push(item);
        historyCalls.push({ id: item.id, name: item.name, arguments: item.arguments });
      }
    }
    const batchId = `pi-prepared-${nextBatchId++}`;
    retainedPreparedBatch = { batchId, prepared: preparedInternal };
    return {
      schema_version: "bb.pi-native.v1",
      kind: "prepared",
      calls,
      ...(errors.length ? { errors } : {}),
      history_calls: historyCalls,
    };
  }
  if (operation === "close") {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length !== 0) {
      fail("close payload must be empty");
    }
    retainedPreparedBatch = null;
    const cleanup = await closeTrackedProcessGroups();
    return { schema_version: "bb.pi-native.v1", kind: "closed", cleanup };
  }
  if (operation === "execute_batch") {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length !== 0) {
      fail("execute_batch payload must be empty");
    }
    if (!retainedPreparedBatch) fail("execute_batch requires the retained prepared batch");
    let completionIndex = 0;
    const completedPreparationErrors = [];
    for (const [sourceIndex, call] of retainedPreparedBatch.prepared.entries()) {
      if (call.error) {
        completedPreparationErrors.push({
          sourceIndex,
          result: {
            id: call.id,
            completion_index: completionIndex++,
            content: [{ type: "text", text: call.error }],
            details: {},
            isError: true,
            terminate: false,
          },
        });
      }
    }
    const validCalls = retainedPreparedBatch.prepared
      .map((call, sourceIndex) => ({ call, sourceIndex }))
      .filter(({ call }) => !call.error);
    const completedValid = await Promise.all(validCalls.map(async ({ call, sourceIndex }) => {
      const request = validateCall(call, defaultCwd);
      const tool = TOOL_FACTORIES[request.toolId](request.cwd);
      const result = await executePrepared({ request, tool, argumentsValue: call.arguments }, signal);
      return {
        sourceIndex,
        result: { id: call.id, completion_index: completionIndex++, ...result },
      };
    }));
    const results = [...completedPreparationErrors, ...completedValid]
      .sort((left, right) => left.sourceIndex - right.sourceIndex)
      .map((entry) => entry.result);
    retainedPreparedBatch = null;
    return { schema_version: "bb.pi-native.v1", kind: "tool_results", results };
  }
  const calls = operation === "batch" ? payload?.calls : null;
  if (Array.isArray(calls)) {
    const results = await Promise.all(calls.map((call) => executeCall(call, defaultCwd, signal)));
    return { results };
  }
  return executeCall(payload, defaultCwd, signal);
}

async function mainOneShot() {
  const input = await readRequest();
  if (input === null || typeof input !== "object" || Array.isArray(input)) fail("request must be a JSON object");
  const controller = new AbortController();
  const abort = () => controller.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);
  try {
    process.stdout.write(`${JSON.stringify(await executeOperation(input.operation ?? "execute", input, controller.signal))}\n`);
  } finally {
    process.removeListener("SIGINT", abort);
    process.removeListener("SIGTERM", abort);
  }
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

(process.env.PI_NATIVE_WORKER_FRAMED === "1" ? mainFramed() : mainOneShot()).catch((error) => {
  console.error(`pi-tools-0.73.1 protocol failure: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
