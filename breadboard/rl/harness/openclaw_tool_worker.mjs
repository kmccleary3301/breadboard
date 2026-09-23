#!/usr/bin/env node
/**
 * OpenClaw 2026.9.4 source-tool phase worker.
 *
 * The worker has no provider/model loop.  It loads only the pinned dist
 * modules, retains one prepared batch, and exposes the six admitted source
 * tools through bb.openclaw-native.v1 phases.
 */
import { pathToFileURL } from "node:url";
import { join, resolve, basename } from "node:path";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import crypto from "node:crypto";

const PROTOCOL = "bb.openclaw-native.v1";
const DIST = process.env.OPENCLAW_DIST || "/opt/openclaw/dist";
const MODULE_DIGESTS = Object.freeze({
  "core-coding-tools-DoP9tAh3.mjs": "403a72188e3378cc570691083fa9270c05e20dc7e2706c62c5c455457b50dca3",
  "bootstrap-DYYMCrXY.mjs": "e87734ad3d2d4b614a317ddd57565df7ce800aff0b8eaafd565066d905d61e1c",
  "workspace-YW5Pl2cf.mjs": "8201a6b4ee921ac2767e272924488ed7c56c9041d274070490a064968438d5cf",
  "bash-process-registry-DHrULGkz.mjs": "6f8a65296ce1a1e07b0f3d94d2bf65f9e88c5a68b9d01df3eecc1f40f349627e",
});
const MAX_LIVE_PROCESSES = 4;
const TOOL_ORDER = Object.freeze(["ls", "read", "edit", "write", "exec", "process"]);

let workspace = null;
let scopeKey = "openclaw:e4";
let tools = new Map();
let sourceBootstrap = null;
let sourceWorkspace = null;
let verifiedRegistryUrl = null;
let prepared = null;
let closing = false;
const pending = new Map();

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
  }
  verifiedRegistryUrl = bytes["bash-process-registry-DHrULGkz.mjs"];
  const core = await import(bytes["core-coding-tools-DoP9tAh3.mjs"]);
  sourceBootstrap = await import(bytes["bootstrap-DYYMCrXY.mjs"]);
  sourceWorkspace = await import(bytes["workspace-YW5Pl2cf.mjs"]);
  return { createCoreCodingTools: core.t, buildBootstrapContextFiles: sourceBootstrap.n, loadWorkspaceBootstrapFiles: sourceWorkspace._ };
}

function admittedSchema(value) {
  if (Array.isArray(value)) return value.map(admittedSchema);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "patternProperties")
      .map(([key, entry]) => [key, admittedSchema(entry)]),
  );
}

function schemaFor(tool) {
  const schema = tool?.parameters ?? tool?.inputSchema ?? tool?.schema ?? { type: "object" };
  const admitted = admittedSchema(schema);
  if (tool.name === "ls" && !Object.hasOwn(admitted, "required")) admitted.required = [];
  return {
    type: "function",
    function: {
      name: tool.name,
      description: text(tool.description),
      parameters: admitted,
    },
  };
}

function makeTools(createCoreCodingTools) {
  const built = createCoreCodingTools({
    codingRoot: workspace,
    containmentRoot: workspace,
    includeBaseCodingTools: true,
    includeShellTools: true,
    readOnly: false,
    workspaceOnly: true,
    execDefaults: {
      host: "gateway",
      security: "full",
      ask: "off",
      allowBackground: true,
      scopeKey,
      cwd: workspace,
    },
    processDefaults: { scopeKey },
  });
  const ordered = TOOL_ORDER.map((name) => built.find((tool) => tool.name === name));
  if (ordered.some((tool) => !tool)) throw new Error("pinned source tool factory did not produce the admitted six-tool set");
  tools = new Map(ordered.map((tool) => [tool.name, tool]));
  return ordered;
}

async function bootstrapContext(loadWorkspaceBootstrapFiles, buildBootstrapContextFiles) {
  const files = await loadWorkspaceBootstrapFiles(workspace);
  return buildBootstrapContextFiles(files, { maxChars: 20000, totalMaxChars: 60000 });
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

async function cleanupScope() {
  const processTool = tools.get("process");
  const observed = [];
  if (!processTool) return { processes: observed, all_dead: true };
  const list = async () => {
    const result = await processTool.execute("worker-cleanup-list", { action: "list" });
    return Array.isArray(result?.details?.sessions) ? result.details.sessions : [];
  };
  let sessions = await list();
  for (const session of sessions) {
    if (session?.sessionId) {
      observed.push({ sessionId: String(session.sessionId), before: session.status, pid: session.pid ?? null });
      if (session.status === "running" || session.status === "backgrounded") {
        await processTool.execute("worker-cleanup-kill", { action: "kill", sessionId: session.sessionId });
      }
    }
  }
  // Source's registry owns PTY descendants.  Wait for it, then independently
  // observe the empty scope; ambiguous cleanup is not reported as clean.
  if (!verifiedRegistryUrl) throw new Error("pinned process registry was not verified");
  const registry = await import(verifiedRegistryUrl);
  if (typeof registry.x === "function") await registry.x(scopeKey);
  for (let attempt = 0; attempt < 20; attempt += 1) {
    sessions = await list();
    const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded");
    if (live.length === 0) {
      return {
        processes: observed.map((item) => ({ ...item, after: "dead" })),
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
  return { processes: observed, all_dead: false };
}

function deliveryId() { return `delivery_${crypto.randomUUID()}`; }

async function executePrepared() {
  if (!prepared) throw new Error("execute_batch requires prepare_tools");
  const results = [];
  for (let index = 0; index < prepared.length; index += 1) {
    const call = prepared[index];
    if (call.error) {
      results.push({ id: call.id, completion_index: index, content: [{ type: "text", text: call.error }], details: {}, isError: true });
      continue;
    }
    const tool = tools.get(call.name);
    if (!tool) {
      results.push({ id: call.id, completion_index: index, content: [{ type: "text", text: `undeclared tool ${call.name}` }], details: {}, isError: true });
      continue;
    }
    try {
      if (call.name === "exec" && (call.arguments?.background === true || call.arguments?.pty === true)) {
        const listed = await tools.get("process").execute("worker-live-limit", { action: "list" });
        const sessions = Array.isArray(listed?.details?.sessions) ? listed.details.sessions : [];
        const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded").length;
        if (live >= MAX_LIVE_PROCESSES) {
          results.push({ id: call.id, completion_index: index, content: [{ type: "text", text: `OpenClaw live process cap exceeded (${MAX_LIVE_PROCESSES})` }], details: { status: "rejected", maxLiveProcesses: MAX_LIVE_PROCESSES }, isError: true });
          continue;
        }
      }
      const result = await tool.execute(call.id, call.arguments);
      const details = result?.details && typeof result.details === "object" ? result.details : {};
      const status = details.status;
      const item = { id: call.id, completion_index: index, content: result?.content ?? [], details, isError: Boolean(result?.isError) };
      if (call.name === "process" && call.arguments?.action === "poll" && details.sessionId) {
        const id = deliveryId();
        pending.set(id, { sessionId: String(details.sessionId), createdAt: Date.now() });
        item.delivery_id = id;
      }
      results.push(item);
    } catch (error) {
      results.push({ id: call.id, completion_index: index, content: [{ type: "text", text: text(error?.message || error) }], details: {}, isError: true });
    }
  }
  prepared = null;
  return { schema_version: PROTOCOL, kind: "tool_results", results };
}

async function handle(message) {
  const phase = message?.phase || message?.operation;
  if (phase === "initialize") {
    if (typeof message.workspace !== "string" || !message.workspace) throw new Error("workspace is required");
    workspace = resolve(message.workspace);
    scopeKey = typeof message.scopeKey === "string" && message.scopeKey ? message.scopeKey : scopeKey;
    const source = await verifyAndLoad();
    let assets = message.bootstrap_assets;
    if (!Array.isArray(assets) && typeof message.package_dir === "string") {
      assets = [];
      for (const name of ["AGENTS.md", "SOUL.md"]) {
        const content = await readFile(join(message.package_dir, "bootstrap", name), "utf8");
        assets.push({ name, content, sha256: `sha256:${sha256(Buffer.from(content, "utf8"))}` });
      }
    }
    await materializeBootstrapAssets(assets);
    const ordered = makeTools(source.createCoreCodingTools);
    const bootstrapFiles = await bootstrapContext(source.loadWorkspaceBootstrapFiles, source.buildBootstrapContextFiles);
    const advertisement = message.advertisement && typeof message.advertisement === "object" ? message.advertisement : {};
    return {
      schema_version: PROTOCOL,
      kind: "initialized",
      system_prompt: text(message.system_prompt || advertisement.system_prompt)
        .replaceAll("{{task}}", text(message.task)),
      tool_schemas: ordered.map(schemaFor),
      bootstrap: { files: bootstrapFiles },
      tools: TOOL_ORDER,
    };
  }
  if (!workspace || tools.size !== TOOL_ORDER.length) throw new Error("worker is not initialized");
  if (phase === "project_request") return { schema_version: PROTOCOL, kind: "request", messages: Array.isArray(message.messages) ? message.messages : [], tools: TOOL_ORDER.map((name) => schemaFor(tools.get(name))) };
  if (phase === "prepare_tools") {
    if (!Array.isArray(message.calls)) throw new Error("prepare_tools calls must be an array");
    prepared = message.calls.map((call, index) => {
      const id = String(call?.id ?? `call_${index}`);
      if (!call || typeof call.name !== "string" || !call.name) return { id, name: "", arguments: {}, error: "tool call has no name" };
      const tool = tools.get(call.name);
      if (!tool) return { id, name: call.name, arguments: call.arguments ?? {}, error: `undeclared tool ${call.name}` };
      if (!call.arguments || typeof call.arguments !== "object" || Array.isArray(call.arguments)) return { id, name: call.name, arguments: {}, error: "tool arguments must be an object" };
      try {
        // This is the pinned source's prepareArguments hook.  Python never
        // rewrites the decoder-finalized arguments.
        const argumentsValue = typeof tool.prepareArguments === "function"
          ? (tool.prepareArguments(call.arguments) ?? {})
          : call.arguments;
        if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) throw new Error("prepared arguments must be an object");
        return { id, name: call.name, arguments: argumentsValue };
      } catch (error) {
        return { id, name: call.name, arguments: call.arguments, error: text(error?.message || error) };
      }
    });
    return {
      schema_version: PROTOCOL,
      kind: "prepared",
      calls: prepared,
      history_calls: prepared.map(({ id, name, arguments: argumentsValue }) => ({ id, name, arguments: argumentsValue })),
    };
  }
  if (phase === "execute_batch") return await executePrepared();
  if (phase === "ack") {
    const id = String(message.delivery_id || "");
    if (!id || !pending.has(id)) throw new Error(`unknown delivery_id ${id}`);
    const record = pending.get(id);
    pending.delete(id);
    return { schema_version: PROTOCOL, kind: "acknowledged", delivery_id: id, session_id: record.sessionId, history_digest: text(message.history_digest) };
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
process.once("SIGTERM", () => void shutdown(0));
process.once("SIGINT", () => void shutdown(130));

let input = Buffer.alloc(0);
let chain = Promise.resolve();
let finishInput;
const inputDone = new Promise((resolvePromise) => { finishInput = resolvePromise; });

async function dispatch(command) {
  try {
    const payload = command.payload && typeof command.payload === "object" ? command.payload : {};
    const message = { ...payload, phase: command.operation };
    const result = await handle(message);
    writeFrame({ schema_version: "bb.native-worker.rpc.v1", request_id: command.request_id, result });
  } catch (error) {
    const message = error instanceof Error ? error.message : text(error);
    writeFrame({
      schema_version: "bb.native-worker.rpc.v1",
      request_id: command.request_id,
      error: { type: "OpenClawWorkerError", message },
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

process.stdin.on("data", (chunk) => {
  input = Buffer.concat([input, chunk]);
  drainInput();
});
process.stdin.on("end", async () => {
  await chain;
  finishInput();
});
await inputDone;
await shutdown(0);
