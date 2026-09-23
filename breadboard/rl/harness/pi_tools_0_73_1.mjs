#!/usr/bin/env node
/**
 * Pinned Pi 0.73.1 native tool worker.
 *
 * One-shot stdin accepts one call or {calls:[...]}; set
 * PI_NATIVE_WORKER_FRAMED=1 for the persistent bb.native-worker.rpc.v1
 * length-prefixed protocol.  In either mode tool preparation and execution
 * run in the pinned Node process, and batch calls share Pi's mutation queue.
 */
import { createHash } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const SCHEMA_VERSION = "bb.native-worker.rpc.v1";
const TOOL_IDS = new Set(["read", "bash", "edit", "write"]);

function packageImport(nodeModules, packageName, entry) {
  if (!nodeModules) return import(packageName);
  return import(pathToFileURL(resolve(nodeModules, packageName, entry)).href);
}

const nodeModules = process.env.PI_CODING_AGENT_NODE_MODULES;
const codingAgent = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/index.js");
const piAi = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/index.js");
const openaiCompletions = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/providers/openai-completions.js");
const promptModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/system-prompt.js");
const resourceModule = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/core/resource-loader.js");
const { createBashTool, createEditTool, createReadTool, createWriteTool } = codingAgent;
const { validateToolArguments } = piAi;
const { convertMessages } = openaiCompletions;
const { buildSystemPrompt } = promptModule;
const { loadProjectContextFiles } = resourceModule;
const TOOL_FACTORIES = Object.freeze({
  bash: (cwd) => createBashTool(cwd),
  edit: (cwd) => createEditTool(cwd),
  read: (cwd) => createReadTool(cwd, { autoResizeImages: true }),
  write: (cwd) => createWriteTool(cwd),
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
  if (argumentsValue === null || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    fail("arguments must be a JSON object");
  }
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
    return {
      content: Array.isArray(result?.content) ? result.content : [],
      details: result?.details ?? {},
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
  return executePrepared(prepareCall(call, defaultCwd), signal);
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
  if ("current_date" in payload || "project_context" in payload) {
    fail("initialize runtime inputs are worker-owned");
  }
  const advertisement = payload.advertisement;
  if (!advertisement || typeof advertisement !== "object" || Array.isArray(advertisement)) {
    fail("initialize requires advertisement");
  }
  const modelConfig = payload.model_config;
  if (!modelConfig || typeof modelConfig !== "object" || Array.isArray(modelConfig)) {
    fail("initialize requires model_config");
  }
  const nativeDescriptionSha256 = originalDescriptionHashes(workspace);
  for (const [name, overlay] of Object.entries(advertisement.tools ?? {})) {
    if (!TOOL_IDS.has(name) || !overlay || typeof overlay !== "object") fail("advertisement tool overlay is invalid");
    const expected = overlay.native_sha256;
    if (expected !== `sha256:${nativeDescriptionSha256[name]}`) fail(`advertisement native description hash mismatch for ${name}`);
  }
  const agentDir = resolve(scratch, "pi-agent");
  const home = resolve(scratch, "home");
  const tmpdir = resolve(scratch, "tmp");
  await mkdir(agentDir);
  await mkdir(home);
  await mkdir(tmpdir);
  process.env.HOME = home;
  process.env.TMPDIR = tmpdir;
  const projectContext = loadProjectContextFiles({ cwd: workspace, agentDir });
  const schemas = toolSchemas(workspace, advertisement);
  const snippets = Object.fromEntries([...TOOL_IDS].map((name) => {
    const tool = TOOL_FACTORIES[name](workspace);
    const replacement = advertisement?.tools?.[name]?.description;
    return [name, typeof replacement === "string" ? replacement : tool.promptSnippet ?? tool.description ?? ""];
  }));
  const promptGuidelines = [...TOOL_IDS].flatMap((name) => {
    const tool = TOOL_FACTORIES[name](workspace);
    return Array.isArray(tool.promptGuidelines) ? tool.promptGuidelines : [];
  });
  const oldTz = process.env.TZ;
  const oldPackageDir = process.env.PI_PACKAGE_DIR;
  process.env.TZ = "UTC";
  process.env.PI_PACKAGE_DIR = packageDir;
  let systemPrompt;
  let currentDate;
  try {
    currentDate = new Date().toISOString().slice(0, 10);
    systemPrompt = buildSystemPrompt({
      cwd: workspace,
      contextFiles: projectContext,
      selectedTools: [...TOOL_IDS],
      toolSnippets: snippets,
      promptGuidelines,
    });
    for (const removal of advertisement.prompt?.remove_exact ?? []) {
      if (typeof removal !== "string" || systemPrompt.split(removal).length !== 2) {
        fail("advertisement prompt removal did not match exactly once");
      }
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

function modelForProject(config) {
  const source = config && typeof config === "object" ? config : {};
  return {
    id: String(source.id ?? source.model ?? "pi-0.73.1"),
    name: String(source.name ?? source.id ?? source.model ?? "pi-0.73.1"),
    api: "openai-completions",
    provider: String(source.provider ?? "openai"),
    baseUrl: String(source.base_url ?? source.baseUrl ?? ""),
    reasoning: false,
    input: Array.isArray(source.input) ? source.input : ["text"],
    cost: source.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: Number(source.context_window ?? source.contextWindow ?? 128000),
    maxTokens: Number(source.max_tokens ?? source.maxTokens ?? 4096),
    compat: source.compat && typeof source.compat === "object" ? source.compat : {},
  };
}

function projectRequest(payload) {
  if (!initializedState) fail("project_request requires initialize");
  if (!Array.isArray(payload?.messages)) fail("project_request requires messages");
  const model = modelForProject(initializedState.modelConfig);
  const acceptsImage = model.input.includes("image");
  const messages = payload.messages.map((message) => {
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
        historyCalls.push({ ...item });
      } catch (error) {
        const message = String(error?.message ?? error);
        const item = {
          id: String(call?.call_id ?? call?.callId ?? call?.id ?? ""),
          name: String(call?.tool_id ?? call?.toolId ?? call?.name ?? ""),
          arguments: call?.arguments ?? {},
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
    return { schema_version: "bb.pi-native.v1", kind: "closed", cleanup: { processes: [], all_dead: true } };
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
