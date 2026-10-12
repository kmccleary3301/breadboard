#!/usr/bin/env node
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { TOOL_IDS, buildSystemPrompt, fail, loadProjectContextFiles, modelForProject, requiredString } from "./pi_0_73_1_runtime.mjs";
import { TOOL_DEFINITION_FACTORIES, close, executeBatch, executeCall, originalDescriptionHashes, prepareTools, toolSchemas, toolState } from "./pi_0_73_1_tools.mjs";
import { parseProviderUsage, replayState } from "./pi_0_73_1_replay.mjs";
import { parseStreamingJsonBatch, projectProviderFailure, projectRequest } from "./pi_0_73_1_provider.mjs";
import { finalizeCompactionPhase, prepareCompactionPhase } from "./pi_0_73_1_compaction.mjs";

















/**
 * Pinned Pi 0.73.1 native tool worker.
 *
 * One-shot stdin accepts one call or {calls:[...]}; set
 * PI_NATIVE_WORKER_FRAMED=1 for the persistent bb.native-worker.rpc.v1
 * length-prefixed protocol.  In either mode tool preparation and execution
 * run in the pinned Node process, and batch calls share Pi's mutation queue.
 */

const MAX_REQUEST_BYTES = 1024 * 1024;
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const SCHEMA_VERSION = "bb.native-worker.rpc.v1";

let initializedState = null;

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
  toolState.retainedPreparedBatch = null;
  return {
    schema_version: "bb.pi-native.v1",
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: schemas,
    bootstrap: initializedState.bootstrap,
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
  if (operation === "project_request") return projectRequest(initializedState, payload);
  if (operation === "parse_provider_usage") {
    const usage = await parseProviderUsage(payload.usage, replayState.replayClock, modelForProject(initializedState.modelConfig));
    return { schema_version: "bb.pi-native.v1", kind: "assistant_usage", usage };
  }
  if (operation === "prepare_compaction") return prepareCompactionPhase(initializedState, payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(initializedState, payload);
  if (operation === "project_provider_failure") return projectProviderFailure(initializedState, payload);
  if (operation === "parse_streaming_json_batch") return parseStreamingJsonBatch(payload);
  if (operation === "prepare_tools") return prepareTools(payload, defaultCwd);
  if (operation === "close") return close(payload);
  if (operation === "execute_batch") return executeBatch(payload, defaultCwd, signal);
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





