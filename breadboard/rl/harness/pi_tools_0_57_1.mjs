#!/usr/bin/env node
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { DefaultResourceLoader, INITIALIZE_KEYS, PHASE_SCHEMA_VERSION, RUNTIME_INPUT_KEYS, SettingsManager, TOOL_ORDER, buildSystemPrompt, fail, getToolPath, modelFromConfig, requireExactKeys, requiredString, verifyPinnedVersions } from "./pi_0_57_1_runtime.mjs";
import { close, createToolRegistry, executeBatch, prepareTools, toolState } from "./pi_0_57_1_tools.mjs";
import { parseProviderUsage, replayState } from "./pi_0_57_1_replay.mjs";
import { parseStreamingJsonBatch, projectProviderFailure, projectRequest } from "./pi_0_57_1_provider.mjs";
import { finalizeCompactionPhase, prepareCompactionPhase } from "./pi_0_57_1_compaction.mjs";

















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

const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const SCHEMA_VERSION = "bb.native-worker.rpc.v1";

let initializedState = null;

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
  toolState.retainedPreparedBatch = null;
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: schemas,
    bootstrap: initializedState.bootstrap,
  };
}

async function executeOperation(operation, payload, signal) {
  if (operation === "initialize") return initialize(payload);
  if (operation === "project_request") return projectRequest(initializedState, payload);
  if (operation === "parse_provider_usage") {
    const usage = await parseProviderUsage(payload.usage, replayState.replayClock, initializedState.model);
    return { schema_version: PHASE_SCHEMA_VERSION, kind: "assistant_usage", usage };
  }
  if (operation === "parse_streaming_json_batch") return parseStreamingJsonBatch(payload);
  if (operation === "prepare_tools") return prepareTools(initializedState, payload);
  if (operation === "execute_batch") return executeBatch(payload, signal);
  if (operation === "project_provider_failure") return projectProviderFailure(initializedState, payload);
  if (operation === "prepare_compaction") return prepareCompactionPhase(initializedState, payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(initializedState, payload);
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


