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
  return {
    id: config.id,
    name: config.name,
    api: config.api,
    provider: config.provider,
    baseUrl: config.baseUrl,
    reasoning: config.reasoning,
    input: [...config.input],
    cost: { ...config.cost },
    contextWindow: config.contextWindow,
    maxTokens: config.maxTokens,
    compat: { ...config.compat },
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
    if (!isPlainObject(message) || !["user", "assistant", "toolResult"].includes(message.role)) {
      fail("messages must be pinned user, assistant, or toolResult AgentMessages");
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
  // openai@6.26.0 client.mjs:351-353,362 (makeRequest) and :193-195
  // (makeStatusError): safeJSON(errText), message only when the body is not
  // JSON, then APIError.generate(status, errJSON, errMessage, response.headers).
  const errJSON = safeJSON(errText);
  const errMessage = errJSON ? undefined : errText;
  const error = APIError.generate(status, errJSON, errMessage, new Headers());
  // The SDK error surfaces from client.chat.completions.create
  // (openai-completions.js:56); throwing it from onPayload (:52) reaches the
  // same pinned catch (:239-249), which builds the terminal assistant message.
  const message = await runPinnedStream(payload.messages, () => {
    throw error;
  }, new AbortController().signal);
  if (message.stopReason !== "error") fail("pinned provider failure did not produce an error message");
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

async function executeOperation(operation, payload, signal) {
  if (operation === "initialize") return initialize(payload);
  if (operation === "project_request") return projectRequest(payload);
  if (operation === "parse_streaming_json_batch") return parseStreamingJsonBatch(payload);
  if (operation === "prepare_tools") return prepareTools(payload);
  if (operation === "execute_batch") return executeBatch(payload, signal);
  if (operation === "project_provider_failure") return projectProviderFailure(payload);
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
