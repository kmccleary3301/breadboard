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
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import { classifyAgentExecResult, exitCodeForEnvelope } from "openclaw:pinned-agent-exec";

const PROTOCOL = "bb.openclaw-native.v1";
const DIST = process.env.OPENCLAW_DIST || "/opt/openclaw/dist";
const MODULE_DIGESTS = Object.freeze({
  "core-coding-tools-DoP9tAh3.mjs": "403a72188e3378cc570691083fa9270c05e20dc7e2706c62c5c455457b50dca3",
  "bootstrap-DYYMCrXY.mjs": "e87734ad3d2d4b614a317ddd57565df7ce800aff0b8eaafd565066d905d61e1c",
  "workspace-YW5Pl2cf.mjs": "8201a6b4ee921ac2767e272924488ed7c56c9041d274070490a064968438d5cf",
  "bash-process-registry-DHrULGkz.mjs": "6f8a65296ce1a1e07b0f3d94d2bf65f9e88c5a68b9d01df3eecc1f40f349627e",
  "openai-transport-stream-D950WgL3.mjs": "83fd60ff0760bef6eeabcee42fd219f1213cc9ffe3f65666681f486775032d78",
  "tool-execution-context-C6v2UVPI.mjs": "17e1286b50ee915fa28d5741614a48295994c978e4a64b77ea6b163f674a0082",
  "internal-hooks-DUPhyX-W.mjs": "7288bc46b3e51f1c86e225b14d6baa84d34a806fad0cb37234c4ee5e34b49218",
  "agent-exec-BAuhpelg.mjs": "2e39dbc961337936860849aaaed6a26b734d0c20648093f2bc51a46ebfe9526d",
});
const MAX_LIVE_PROCESSES = 4;
const TOOL_ORDER = Object.freeze(["edit", "exec", "ls", "process", "read", "write"]);

let workspace = null;
let scopeKey = "openclaw:e4";
let tools = new Map();
let sourceBootstrap = null;
let sourceWorkspace = null;
let sourceTransport = null;
let modelConfig = null;
let sourceExecutionContext = null;
let sourceAcknowledgeResult = null;
let builtTools = [];
let verifiedRegistryUrl = null;
let prepared = null;
let preparedContext = null;
let closing = false;
let advertisedTools = new Map();
let capabilityDenials = new Map();
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
  sourceExecutionContext = await import(bytes["tool-execution-context-C6v2UVPI.mjs"]);
  sourceAcknowledgeResult = (await import(bytes["internal-hooks-DUPhyX-W.mjs"])).t;
  sourceTransport = await import(bytes["openai-transport-stream-D950WgL3.mjs"]);
  return {
    createCoreCodingTools: core.t,
    buildBootstrapContextFiles: sourceBootstrap.n,
    loadWorkspaceBootstrapFiles: sourceWorkspace._,
    buildOpenAICompletionsParams: sourceTransport.t,
  };
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

function validateAdvertisement(value) {
  exactKeys(value, ["prompt_removals", "system_prompt", "tool_description_replacements", "tools", "capability_denials"], "advertisement");
  if (typeof value.system_prompt !== "string" || !value.system_prompt) {
    throw new Error("advertisement.system_prompt is required");
  }
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
    workspaceOnly: true,
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
    if (message.role !== "tool") return message;
    const raw = Array.isArray(message.content)
      ? message.content.map((part) => typeof part?.text === "string" ? part.text : text(part))
      : [text(message.content)];
    return {
      role: "toolResult",
      toolCallId: text(message.tool_call_id),
      content: raw.map((value) => ({ type: "text", text: value })),
      isError: Boolean(message.isError),
    };
  });
}

function projectSourceRequest(messages, buildOpenAICompletionsParams) {
  if (!modelConfig || typeof modelConfig !== "object") throw new Error("model_config is required");
  const system = messages[0];
  const systemPrompt = (system && system.role === "system" && typeof system.content === "string")
    ? system.content
    : "";
  const history = toSourceHistory(system && system.role === "system" ? messages.slice(1) : messages);
  const baseTools = builtTools && builtTools.length ? builtTools : Array.from(tools.values());
  const sourceTools = baseTools.map((tool) => {
    const overlay = advertisedTools.get(tool.name);
    return overlay ? { ...tool, description: overlay.description } : tool;
  });
  if (typeof buildOpenAICompletionsParams === "function" && systemPrompt) {
    const params = buildOpenAICompletionsParams(
      modelConfig,
      { systemPrompt, messages: history, tools: sourceTools },
      undefined,
    );
    return {
      messages: params.messages || [],
      tools: params.tools || [],
    };
  }
  return {
    messages,
    tools: baseTools.map(schemaFor),
  };
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

async function markerObservation() {
  const marker = `bb-openclaw-marker-${process.pid}-${Date.now()}-${crypto.randomUUID()}`;
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)", marker], {
    detached: true,
    stdio: "ignore",
  });
  child.unref();
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 25));
  const before = processSnapshot().filter((item) => item.argv.includes(marker));
  const leader = before.find((item) => item.pid === child.pid);
  if (!leader || leader.pgid !== child.pid) {
    throw new Error("OS process observer marker group was not proven");
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
  const listed = calls.some((call) => call.name === "exec" && !call.error)
    ? await tools.get("process").execute("worker-live-limit", { action: "list" })
    : null;
  const sessions = Array.isArray(listed?.details?.sessions) ? listed.details.sessions : [];
  const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded").length;
  let reserved = 0;
  let completionIndex = 0;
  const run = async (call) => {
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
    if (
      !message.advertisement
      || typeof message.advertisement !== "object"
      || Array.isArray(message.advertisement)
      || typeof message.advertisement.system_prompt !== "string"
      || !message.advertisement.system_prompt
    ) {
      throw new Error("advertisement.system_prompt is required");
    }
    const advertisement = validateAdvertisement(message.advertisement);
    workspace = resolve(workspacePath);
    scopeKey = typeof message.scopeKey === "string" && message.scopeKey ? message.scopeKey : scopeKey;
    if (message.model_config && (typeof message.model_config !== "object" || Array.isArray(message.model_config))) {
      throw new Error("model_config must be an object");
    }
    modelConfig = message.model_config || null;
    let assets = message.bootstrap_assets;
    const packageDir = typeof message.package_dir === "string" && message.package_dir ? message.package_dir : runtimeInputs.package_dir;
    if (!Array.isArray(assets) && typeof packageDir === "string") {
      assets = [];
      for (const name of ["AGENTS.md", "SOUL.md"]) {
        const content = await readFile(join(packageDir, "bootstrap", name), "utf8");
        assets.push({ name, content, sha256: `sha256:${sha256(Buffer.from(content, "utf8"))}` });
      }
    }
    await materializeBootstrapAssets(assets);
    const source = await verifyAndLoad();
    const ordered = makeTools(source.createCoreCodingTools);
    const bootstrapFiles = await bootstrapContext(source.loadWorkspaceBootstrapFiles, source.buildBootstrapContextFiles);
    return {
      schema_version: PROTOCOL,
      kind: "initialized",
      system_prompt: text(message.system_prompt || advertisement.system_prompt)
        .replaceAll("{{task}}", text(message.task)),
      tool_schemas: ordered.map(schemaFor),
      bootstrap: { files: bootstrapFiles, ...runtimeInputs },
      tools: TOOL_ORDER,
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
    prepared = message.calls.map((call, index) => {
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
    const runResult = message.result && typeof message.result === "object"
      ? message.result
      : buildRunResultFromTerminalState(message);
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
