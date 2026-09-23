#!/opt/omp/runtime/bun-linux-x64-baseline/bun
import { readdir, readFile } from "node:fs/promises";

// Persistent tool-only phase worker. The Conductor owns provider transport and
// the model loop; this process only composes and executes the four SDK tools.
const RPC_SCHEMA = "bb.native-worker.rpc.v1";
const PHASE_SCHEMA = "bb.omp-native.v1";
const SOURCE_ROOT = "/opt/omp/source/oh-my-pi-3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec";
const TOOL_NAMES = ["read", "bash", "edit", "write"] as const;
type Call = { id: string; name: string; arguments: Record<string, unknown> };
let boundedDescriptions: Record<string, string> = {};
let nativeSystemPrompt = "";
let session: any = null;
let tools: any[] = [];
let irToJsonSchema: ((ir: unknown, options?: Record<string, unknown>) => Record<string, unknown>) | null = null;
let prepared: Array<Call & { error?: string }> = [];

function sleep(milliseconds: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, milliseconds);
  return promise;
}
type ProcessHandle = { pid: number; pgid: number };
type ProcessInfo = ProcessHandle & { ppid: number; state: string };

async function processTable(): Promise<Map<number, ProcessInfo>> {
  const table = new Map<number, ProcessInfo>();
  for (const entry of await readdir("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    try {
      const stat = await readFile(`/proc/${entry}/stat`, "utf8");
      const match = stat.match(/^(\d+) \(.+\) (\S+) (\d+) (\d+)/);
      if (match) {
        table.set(Number(match[1]), {
          pid: Number(match[1]),
          state: match[2],
          ppid: Number(match[3]),
          pgid: Number(match[4]),
        });
      }
    } catch {
      // Processes can exit while procfs is being sampled.
    }
  }
  return table;
}

async function descendantHandles(root: number): Promise<ProcessHandle[]> {
  const table = await processTable();
  const found = new Set<number>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const info of table.values()) {
      if ((info.ppid === root || found.has(info.ppid)) && !found.has(info.pid)) {
        found.add(info.pid);
        changed = true;
      }
    }
  }
  return [...found].flatMap((pid) => {
    const info = table.get(pid);
    return info ? [{ pid: info.pid, pgid: info.pgid }] : [];
  });
}

function provenGroups(handles: ProcessHandle[], table: Map<number, ProcessInfo>): number[] {
  const own = table.get(process.pid)?.pgid;
  return [...new Set(handles.map((handle) => handle.pgid))].filter((pgid) => {
    if (!pgid || pgid === own) return false;
    const members = [...table.values()].filter((info) => info.pgid === pgid);
    return handles.some((handle) => handle.pgid === pgid && members.some((member) => member.pid === handle.pid));
  });
}

function signalGroups(groups: number[], kind: "SIGTERM" | "SIGKILL"): void {
  for (const pgid of groups) {
    try {
      process.kill(-pgid, kind);
    } catch {
      // The group leader exited during cleanup.
    }
  }
}

async function remainingGroups(groups: number[]): Promise<number[]> {
  const table = await processTable();
  return groups.filter((pgid) => [...table.values()].some((info) => info.pgid === pgid));
}

async function reapDescendants(owned: ProcessHandle[] = []): Promise<number[]> {
  const handles = new Map<number, ProcessHandle>();
  for (const handle of owned) handles.set(handle.pid, handle);
  for (const handle of await descendantHandles(process.pid)) handles.set(handle.pid, handle);
  const groups = provenGroups([...handles.values()], await processTable());
  signalGroups(groups, "SIGTERM");
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const remaining = await remainingGroups(groups);
    if (!remaining.length) return [];
    await sleep(20);
  }
  await sleep(50);
  const remaining = await remainingGroups(groups);
  const table = await processTable();
  return remaining.flatMap((pgid) => [...table.values()].filter((info) => info.pgid === pgid).map((info) => info.pid));
}
let workspace = "";
let convertMessages: ((model: any, context: any, compat: any) => unknown[]) | null = null;
const converterModel = {
  id: "capture",
  provider: "capture",
  api: "openai-completions",
  reasoning: false,
  input: ["text"],
  compat: {},
};

function exactRecord(value: unknown, label: string, required: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  const record = value as Record<string, unknown>;
  const allowed = new Set([...required, ...optional]);
  const actual = Object.keys(record);
  if (actual.some((key) => !allowed.has(key)) || required.some((key) => !Object.hasOwn(record, key))) {
    throw new Error(`${label} has invalid keys`);
  }
  return record;
}

function requireAdvertisement(value: unknown): {
  systemPrompt: string;
  descriptions: Record<string, string>;
  capabilityDenials: Record<string, Record<string, unknown>>;
} {
  const advertisement = exactRecord(value, "advertisement", ["system_prompt", "tool_descriptions", "capability_denials"], ["settings"]);
  if (typeof advertisement.system_prompt !== "string") throw new Error("advertisement.system_prompt must be a string");
  const typedDescriptions = exactRecord(advertisement.tool_descriptions, "advertisement.tool_descriptions", TOOL_NAMES);
  const bounded: Record<string, string> = {};
  for (const name of TOOL_NAMES) {
    if (typeof typedDescriptions[name] !== "string") throw new Error(`advertisement tool description must be a string: ${name}`);
    bounded[name] = typedDescriptions[name] as string;
  }
  const rawDenials = exactRecord(advertisement.capability_denials, "advertisement.capability_denials", ["pty", "async"]);
  const capabilityDenials: Record<string, Record<string, unknown>> = {};
  for (const capability of ["pty", "async"]) {
    const entry = exactRecord(rawDenials[capability], `advertisement.capability_denials.${capability}`, ["schema_version", "capability", "message", "source_ref"]);
    if (
      entry.schema_version !== "bb.omp-capability-denial.v1"
      || entry.capability !== capability
      || typeof entry.message !== "string"
      || typeof entry.source_ref !== "string"
    ) throw new Error(`advertisement has invalid capability denial: ${capability}`);
    capabilityDenials[capability] = entry;
  }
  if (advertisement.settings !== undefined) {
    exactRecord(advertisement.settings, "advertisement.settings", ["request_cap", "model_max_tokens", "provider_attempts"]);
  }
  return { systemPrompt: advertisement.system_prompt, descriptions: bounded, capabilityDenials };
}
function parametersFor(tool: any): Record<string, unknown> {
  if (irToJsonSchema === null || typeof tool.parameters !== "function" || tool.parameters.ir === undefined) {
    throw new Error(`tool schema is unavailable: ${tool.name}`);
  }
  const source = irToJsonSchema(tool.parameters.ir, { io: "input", dialect: null });
  const properties = source.properties;
  if (properties === null || typeof properties !== "object" || Array.isArray(properties)) {
    throw new Error(`tool schema is not an object: ${tool.name}`);
  }
  const required = source.required;
  if (required !== undefined && (!Array.isArray(required) || required.some((name) => typeof name !== "string"))) {
    throw new Error(`tool schema required list is invalid: ${tool.name}`);
  }
  return {
    ...source,
    additionalProperties: false,
    properties: {
      i: { type: "string", description: "concise intent" },
      ...properties,
    },
    required: [...(required ?? []), "i"],
  };
}
async function initialize(payload: Record<string, any>) {
  if (payload.workspace === undefined || payload.scratch === undefined) throw new Error("initialize requires injected workspace and scratch");
  const advertisement = requireAdvertisement(payload.advertisement);
  workspace = String(payload.workspace);
  nativeSystemPrompt = advertisement.systemPrompt;
  boundedDescriptions = advertisement.descriptions;
  // The pinned source root is deployment-selected; static source imports cannot
  // represent the verified runtime path used by the SIF installation.
  const { createAgentSession, Settings } = await import(`${SOURCE_ROOT}/packages/coding-agent/src/sdk.ts`);
  const providerModule = await import(`${SOURCE_ROOT}/packages/ai/src/providers/openai-completions.ts`);
  convertMessages = providerModule.convertMessages as typeof convertMessages;
  const schemaModule = await import(`${SOURCE_ROOT}/packages/omptype/src/json-schema.ts`);
  irToJsonSchema = schemaModule.irToJsonSchema as typeof irToJsonSchema;
  const { SessionManager } = await import(`${SOURCE_ROOT}/packages/coding-agent/src/session/session-manager.ts`);
  const settings = await Settings.init({ cwd: workspace, agentDir: String(payload.scratch), inMemory: true, configFiles: [], overrides: {
    "retry.enabled": false, "retry.fallbackChains": {}, "compaction.enabled": false,
    "modelLoopGuard.enabled": false, "tools.maxTimeout": 30, "edit.fuzzyMatch": true,
    "edit.fuzzyThreshold": 0.95, "edit.enforceSeenLines": true, "edit.autoRepair.enabled": false,
    "edit.blockAutoGenerated": true, "shellMinimizer.enabled": false,
  } });
  const manager = SessionManager.inMemory(workspace);
  const created = await createAgentSession({
    cwd: workspace, agentDir: String(payload.scratch), modelPattern: "capture/capture", thinkingLevel: "off",
    toolNames: [...TOOL_NAMES], restrictToolNames: true, allowRestrictedCustomTools: false,
    settings, sessionManager: manager, contextFiles: [], skills: [], rules: [], promptTemplates: [],
    slashCommands: [], customTools: [], extensions: [], additionalExtensionPaths: [],
    disableExtensionDiscovery: true, enableMCP: false, enableLsp: false, enableIrc: false,
    skipPythonPreflight: true, hasUI: false, interactivePrompts: false,
    rebindModelAfterDiscovery: false, getApiKey: async () => "omp-tool-worker-key",
  });
  session = created.session;
  tools = session.agent.state.tools.filter((tool: any) => TOOL_NAMES.includes(tool.name));
  return {
    schema_version: PHASE_SCHEMA,
    kind: "initialized",
    system_prompt: advertisement.systemPrompt,
    tool_schemas: tools.map((tool: any) => ({ type: "function", function: { name: tool.name, description: boundedDescriptions[tool.name], parameters: parametersFor(tool) } })),
    bootstrap: {
      consumer_id: "breadboard.oh-my-pi.v18.1.17",
      workspace,
      source_commit: SOURCE_ROOT.split("-").at(-1),
      settings: payload.advertisement.settings ?? {},
      capability_denials: advertisement.capabilityDenials,
    },
  };
}

async function dispatch(operation: string, payload: Record<string, any>): Promise<Record<string, any>> {
  if (operation === "initialize") return initialize(payload);
  if (!session) throw new Error("worker must be initialized before phases");
  if (operation === "project_request") {
    if (convertMessages === null) throw new Error("pinned OMP provider converter is unavailable");
    const model = session.agent.state.model ?? converterModel;
    if (model.api !== "openai-completions") throw new Error(`pinned OMP provider converter requires an OpenAI Completions model: ${String(model.api)}`);
    const messages = convertMessages(model, { systemPrompt: nativeSystemPrompt ? [nativeSystemPrompt] : [], messages: payload.messages ?? [] }, model.compat);
    return {
      schema_version: PHASE_SCHEMA,
      kind: "request",
      messages,
      tools: tools.map((tool: any) => ({
        type: "function",
        function: { name: tool.name, description: boundedDescriptions[tool.name], parameters: parametersFor(tool) },
      })),
    };
  }
  if (operation === "prepare_tools") {
    prepared = (payload.calls ?? []).map((call: any) => {
      let argumentsValue = call.arguments;
      let error: string | undefined;
      if (typeof argumentsValue === "string") {
        try {
          argumentsValue = JSON.parse(argumentsValue);
        } catch {
          error = "Invalid tool arguments: expected a JSON object";
          argumentsValue = {};
        }
      }
      if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
        error = error ?? "Invalid tool arguments: expected a JSON object";
        argumentsValue = {};
      }
      const item: any = { id: String(call.id), name: String(call.name), arguments: argumentsValue };
      if (!TOOL_NAMES.includes(item.name)) item.error = `OMP tool is not admitted: ${item.name}`;
      if (error) item.error = item.error ?? error;
      return item;
    });
    return { schema_version: PHASE_SCHEMA, kind: "prepared", calls: prepared, history_calls: prepared.map((call) => ({ id: call.id, name: call.name, arguments: call.arguments })) };
  }
  if (operation === "execute_batch") {
    const completed: Array<Record<string, unknown> & { source_index: number }> = [];
    await Promise.all(prepared.map(async (call, sourceIndex) => {
      if (call.error) {
        completed.push({ id: call.id, completion_index: completed.length, content: call.error, details: { phase: "prepare" }, isError: true, source_index: sourceIndex });
        return;
      }
      const tool = tools.find((candidate: any) => candidate.name === call.name);
      if (!tool) {
        completed.push({ id: call.id, completion_index: completed.length, content: `OMP tool is not admitted: ${call.name}`, details: {}, isError: true, source_index: sourceIndex });
        return;
      }
      try {
        const result = await tool.execute(call.id, call.arguments);
        completed.push({ id: call.id, completion_index: completed.length, content: result.content ?? [], details: result.details ?? {}, isError: Boolean(result.details?.isError), terminate: Boolean(result.details?.terminate), source_index: sourceIndex });
      } catch (error) {
        completed.push({ id: call.id, completion_index: completed.length, content: String(error), details: { phase: "execute" }, isError: true, source_index: sourceIndex });
      }
    }));
    completed.sort((left, right) => left.source_index - right.source_index);
    return { schema_version: PHASE_SCHEMA, kind: "tool_results", results: completed.map(({ source_index, ...result }) => result) };
  }
  if (operation === "close") {
    const owned = await descendantHandles(process.pid);
    let disposeError: unknown;
    try {
      await session.dispose();
    } catch (error) {
      disposeError = error;
    }
    session = null;
    tools = [];
    const processes = await reapDescendants(owned);
    if (disposeError) throw disposeError;
    return { schema_version: PHASE_SCHEMA, kind: "closed", cleanup: { processes, all_dead: processes.length === 0 } };
  }
  throw new Error(`unknown OMP phase: ${operation}`);
}

const decoder = new TextDecoder();
const reader = Bun.stdin.stream().getReader();
let buffer = new Uint8Array(0);
function append(chunk: Uint8Array) { const next = new Uint8Array(buffer.length + chunk.length); next.set(buffer); next.set(chunk, buffer.length); buffer = next; }
async function writeFrame(value: Record<string, any>) {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  const frame = new Uint8Array(4 + encoded.length);
  new DataView(frame.buffer).setUint32(0, encoded.length, false);
  frame.set(encoded, 4);
  await Bun.write(Bun.stdout, frame);
}
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  append(value);
  while (buffer.length >= 4) {
    const length = new DataView(buffer.buffer, buffer.byteOffset, 4).getUint32(0, false);
    if (buffer.length < 4 + length) break;
    const payload = JSON.parse(decoder.decode(buffer.slice(4, 4 + length)));
    buffer = buffer.slice(4 + length);
    if (payload.schema_version !== RPC_SCHEMA || typeof payload.request_id !== "number") throw new Error("invalid worker frame");
    try {
      const result = await dispatch(payload.operation, payload.payload);
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, result });
    } catch (error) {
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, error: { type: "WorkerPhaseError", message: String(error) } });
    }
  }
}
