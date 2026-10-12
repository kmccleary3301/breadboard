import { accessSync, constants as fsConstants } from "node:fs";
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";

const TOOL_ORDER = Object.freeze(["edit", "exec", "ls", "process", "read", "write"]);
function prepareToolCallArguments(tool, toolCall) {
  if (!tool.prepareArguments) return toolCall;
  const preparedArguments = tool.prepareArguments(toolCall.arguments);
  if (preparedArguments === toolCall.arguments) return toolCall;
  return {
    ...toolCall,
    arguments: preparedArguments
  };
}

function createErrorToolResult(message, details = {}) {
  return {
    content: [{
      type: "text",
      text: message
    }],
    details
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
export { TOOL_ORDER, prepareToolCallArguments, createErrorToolResult, exactKeys };
class OpenClawMarkerObservationError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "OpenClawMarkerObservationError";
  }
}
export function createToolSession({ supplier, context, protocol, prepareSourceToolCall }) {
let tools = new Map();
let builtTools = [];
let prepared = null;
let preparedContext = null;
let closing = false;
let advertisedTools = new Map();
let capabilityDenials = new Map();
const pending = new Map();
const MAX_LIVE_PROCESSES = 4;
const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
const text = (value) => typeof value === "string" ? value : String(value ?? "");
function episodeSourceTools() {
  return builtTools.map((tool) => {
    const overlay = advertisedTools.get(tool.name);
    return overlay ? { ...tool, description: overlay.description } : tool;
  });
}

function validateAdvertisement(value) {
  exactKeys(value, ["prompt_removals", "tool_description_replacements", "tools", "capability_denials"], "advertisement");
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
    codingRoot: context.workspace,
    containmentRoot: context.workspace,
    includeBaseCodingTools: true,
    includeShellTools: true,
    readOnly: false,
    // core-coding-tools-DoP9tAh3.mjs:1007,1024-1025 derive native read/ls caps.
    modelContextWindowTokens: context.modelConfig.contextWindow,
    // Pinned supplier default: embedded-agent.runtime-DnOK0ORi.mjs:649
    // `permissionToolPolicy?.workspaceOnly ?? false` and agent-tools-DXxcrXNI.mjs:388
    // `fsConfig.workspaceOnly === true`; the capture config sets neither, so the
    // supplier's file tools echo the model's own path. Containment is the lease envelope.
    workspaceOnly: false,
    execDefaults: {
      host: "gateway",
      timeoutSec: 30,
      security: "full",
      ask: "off",
      allowBackground: true,
      scopeKey: context.scopeKey,
      cwd: context.workspace,
    },
    processDefaults: { scopeKey: context.scopeKey },
  });
  builtTools = built;
  const ordered = TOOL_ORDER.map((name) => built.find((tool) => tool.name === name));
  if (ordered.some((tool) => !tool)) throw new Error("pinned source tool factory did not produce the admitted six-tool set");
  tools = new Map(ordered.map((tool) => [tool.name, tool]));
  return ordered;
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

function markerExecutable() {
  // Sealed launches are Linux-only; elsewhere execPath is the worker's image.
  if (process.platform !== "linux") return process.execPath;
  // A sealed launch runs a memfd, so execPath is "/memfd:... (deleted)".  The
  // kernel's /proc/self/exe link still executes that same sealed image.
  const image = "/proc/self/exe";
  try {
    accessSync(image, fsConstants.X_OK);
  } catch (error) {
    throw new OpenClawMarkerObservationError(
      `OS process observer marker image is not executable: ${text(error?.message ?? error)}`,
      { cause: error },
    );
  }
  return image;
}

function spawnMarker(marker) {
  const executable = markerExecutable();
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(executable, ["-e", "setInterval(() => {}, 1000)", marker], {
      detached: true,
      stdio: "ignore",
    });
    // Without a listener a spawn failure is an uncaught crash, not a receipt.
    child.on("error", (error) => rejectPromise(new OpenClawMarkerObservationError(
      `OS process observer marker spawn failed: ${text(error?.message ?? error)}`,
      { cause: error },
    )));
    child.once("spawn", () => {
      child.unref();
      resolvePromise(child);
    });
  });
}

async function markerObservation() {
  // Observer-only nonce: appears in cleanup evidence, never history or a model request.
  const marker = `bb-openclaw-marker-${process.pid}-${Date.now()}-${crypto.randomUUID()}`;
  const child = await spawnMarker(marker);
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 25));
  const before = processSnapshot().filter((item) => item.argv.includes(marker));
  const leader = before.find((item) => item.pid === child.pid);
  if (!leader || leader.pgid !== child.pid) {
    throw new OpenClawMarkerObservationError("OS process observer marker group was not proven");
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
  if (!supplier.verifiedRegistryUrl) throw new Error("pinned process registry was not verified");
  const registry = await import(supplier.verifiedRegistryUrl);
  if (typeof registry.x === "function") await registry.x(context.scopeKey);
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
  const listed = calls.some((call) => call.name === "exec" && !call.error && call.kind !== "immediate")
    ? await tools.get("process").execute("worker-live-limit", { action: "list" })
    : null;
  const sessions = Array.isArray(listed?.details?.sessions) ? listed.details.sessions : [];
  const live = sessions.filter((session) => session?.status === "running" || session?.status === "backgrounded").length;
  let reserved = 0;
  let completionIndex = 0;
  const run = async (call) => {
    if (call.kind === "immediate") {
      return { id: call.id, completion_index: completionIndex++, ...call.result, isError: call.isError };
    }
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
      const result = await supplier.sourceExecutionContext.n({ assistantMessage: context.assistantMessage }, () => tool.execute(call.id, call.arguments));
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
  return { schema_version: protocol, kind: "tool_results", results };
}
async function prepareTools(message) {
    if (!Array.isArray(message.calls)) throw new Error("prepare_tools calls must be an array");
    preparedContext = { assistantMessage: {} };
    prepared = await Promise.all(message.calls.map(async (call, index) => {
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
      const admission = await prepareSourceToolCall(tool, call);
      if (admission.kind === "immediate") return { id, name: call.name, arguments: call.arguments, ...admission };
      return { id, name: call.name, arguments: admission.args };
    }));
    return {
      schema_version: protocol,
      kind: "prepared",
      calls: prepared,
    };
  }
async function acknowledge(message) {
    const id = String(message.delivery_id || "");
    if (!id || !pending.has(id)) throw new Error(`unknown delivery_id ${id}`);
    const record = pending.get(id);
    supplier.sourceAcknowledgeResult(record.result);
    for (const [key, delivery] of pending) {
      if (delivery.sessionId === record.sessionId) pending.delete(key);
    }
    return { schema_version: protocol, kind: "acked", delivery_id: id, session_id: record.sessionId, history_digest: text(message.history_digest) };
  }
async function close() {
    if (closing) throw new Error("worker close already requested");
    const cleanup = await cleanupScope();
    if (!cleanup.all_dead) throw new Error("native process scope did not reach independently observed death");
    closing = true;
    return { schema_version: protocol, kind: "closed", cleanup };
  }
return { validateAdvertisement, schemaFor, makeTools, episodeSourceTools, prepareTools, executePrepared, acknowledge, cleanupScope, close,
 get tools() { return tools; }, get builtTools() { return builtTools; }, get closing() { return closing; },
};
}
