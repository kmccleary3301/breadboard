#!/usr/bin/env bun
import { readdir, readFile } from "node:fs/promises";

// Persistent tool-only phase worker for Oh My Pi 16.2.13.
// The Conductor owns provider transport and the model loop; this process
// composes and executes the pinned SDK tools and projects requests/failures.
const RPC_SCHEMA = "bb.native-worker.rpc.v1";
const PHASE_SCHEMA = "bb.omp-native.v16.2.13";
const TOOL_NAMES = ["read", "bash", "edit", "write"] as const;
const WORKER_API_KEY = "omp-tool-worker-key";

const PINNED_SHA256: Record<string, string> = {
  "@oh-my-pi/pi-coding-agent/src/sdk.ts": "73c7fd74ea75489b630e1962c09c3a6d748e142555c6102055fd85d1e6a616b4",
  "@oh-my-pi/pi-coding-agent/src/config/settings.ts": "38c0bb319d3baa0ed23334599d761665211f53ae24207fa540f75f905163897f",
  "@oh-my-pi/pi-coding-agent/src/config/model-registry.ts": "8610d641efde6cf06a1242356f83c7252ed3860508577b01c5715e7cda8bc763",
  "@oh-my-pi/pi-ai/src/stream.ts": "6669d6e0df952a5592890f5c200005d37e4e5a5ae5f2eb8f37dbb9e5ac3656bc",
  "@oh-my-pi/pi-ai/src/providers/openai-completions.ts": "a1b151be08db377cb755db4cd9160a246c9a30aaed0fc766b454e7a511dc4d49",
  "@oh-my-pi/pi-ai/src/utils/validation.ts": "eafa719221faca75ac8ce5af7168400058ace1d6b8594b8fb61e72b7dcd8b26e",
  "@oh-my-pi/pi-catalog/src/hosts.ts": "74f0701fdee8b803af23034216583ec8fc5d4a8119836abc3c842aafc7cc5811",
  "@oh-my-pi/pi-agent-core/src/agent-loop.ts": "ec162638dd7d6585f4388fbaa92caace47cafc61b27fc9d2601b7cc050375465",
  // Upstream 5356713e patchedDependencies (patches/@ark%2Fschema@0.56.0.patch) applied to @ark/schema@0.56.0,
  // which the 16.2.13 CLI bundle bakes in; it keeps tool schema keys in declaration order.
  "@ark/schema/out/constraint.js": "bdfe5b022a56dd40bf47a93864f18a4b326c21a9b8eca86b977e814bb11bcd98",
};

type Call = { id: string; name: string; arguments: Record<string, unknown> };
type PreparedCall = Call & { error?: string };

let pinnedNodeModules = "";
let workspace = "";
let runtimeInputs: Record<string, string> = {};
let session: any = null;
let tools: any[] = [];
let normalizedTools: any[] = [];
let validateToolArguments: ((tool: any, call: any) => any) | null = null;
let streamSimple: any = null;
let parseStreamingJson: ((input: string | undefined) => any) | null = null;
let preparedCalls: PreparedCall[] = [];
let lengthAbortedMessage = "";
let sanitizeText: (text: string) => string = (t) => t;
let abortReasonText: ((signal: AbortSignal | undefined) => string) | null = null;
let ToolCallBlockedError: new (reason?: string) => Error = Error;
let journalSerial = 0;
let journalView: Array<{ entryId: string; message: Record<string, unknown> }> = [];

async function sha256File(path: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await Bun.file(path).arrayBuffer());
  return Buffer.from(digest).toString("hex");
}

function sleep(milliseconds: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, milliseconds);
  return promise;
}

type ProcessHandle = { pid: number; pgid: number };
type ProcessInfo = ProcessHandle & { ppid: number; state: string };

async function processTable(): Promise<Map<number, ProcessInfo>> {
  const table = new Map<number, ProcessInfo>();
  try {
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
      } catch (err: unknown) {
        if ((err as { code?: string })?.code !== "ENOENT") throw err;
      }
    }
  } catch (err: unknown) {
    if ((err as { code?: string })?.code !== "ENOENT") throw err;
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
  return [...new Set(handles.map((h) => h.pgid))].filter((pgid) => {
    if (!pgid || pgid === own) return false;
    const members = [...table.values()].filter((info) => info.pgid === pgid);
    return handles.some((handle) => handle.pgid === pgid && members.some((member) => member.pid === handle.pid));
  });
}

function signalGroups(groups: number[], kind: "SIGTERM" | "SIGKILL"): void {
  for (const pgid of groups) {
    try {
      process.kill(-pgid, kind);
    } catch (err: unknown) {
      if ((err as { code?: string })?.code !== "ESRCH") throw err;
    }
  }
}

async function remainingGroups(groups: number[]): Promise<number[]> {
  const table = await processTable();
  return groups.filter((pgid) => [...table.values()].some((info) => info.pgid === pgid));
}

async function reapDescendants(owned: ProcessHandle[] = []): Promise<number[]> {
  const handles = new Map<number, ProcessHandle>();
  for (const h of owned) handles.set(h.pid, h);
  for (const h of await descendantHandles(process.pid)) handles.set(h.pid, h);
  const table = await processTable();
  if (table.size === 0) return [];
  const groups = provenGroups([...handles.values()], table);
  signalGroups(groups, "SIGTERM");
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const remaining = await remainingGroups(groups);
    if (!remaining.length) return [];
    await sleep(20);
  }
  await sleep(50);
  const remaining = await remainingGroups(groups);
  const finalTable = await processTable();
  return remaining.flatMap((pgid) => [...finalTable.values()].filter((info) => info.pgid === pgid).map((info) => info.pid));
}

function pinnedSystemPrompt(): string[] {
  // pi-agent-core types.ts:518 declares the session prompt as string[].
  const prompt = session.agent.state.systemPrompt;
  if (!Array.isArray(prompt) || !prompt.every((part: unknown) => typeof part === "string")) {
    throw new Error("pinned agent session system prompt is not string[]");
  }
  return prompt;
}

async function initialize(payload: Record<string, any>): Promise<Record<string, any>> {
  // The sandbox launch environment (sandbox.py native phase environment) owns the root.
  const nodeModulesRoot = process.env.OMP16213_CODING_AGENT_NODE_MODULES;
  if (typeof nodeModulesRoot !== "string" || nodeModulesRoot.length === 0) {
    throw new Error("initialize requires OMP16213_CODING_AGENT_NODE_MODULES");
  }
  pinnedNodeModules = nodeModulesRoot.replace(/\/+$/, "");

  // Fail-closed verification of pinned files
  for (const [relativePath, expectedSha] of Object.entries(PINNED_SHA256)) {
    const filePath = `${pinnedNodeModules}/${relativePath}`;
    let observedSha: string;
    try {
      observedSha = await sha256File(filePath);
    } catch (err: unknown) {
      throw new Error(`pinned OMP 16.2.13 file missing or unreadable: ${relativePath} (${String(err)})`);
    }
    if (observedSha !== expectedSha) {
      throw new Error(
        `pinned OMP 16.2.13 file sha mismatch for ${relativePath}: expected ${expectedSha}, got ${observedSha}`
      );
    }
  }

  // Initialize Bun module resolution path
  process.env.NODE_PATH = pinnedNodeModules;
  const { Module } = await import("node:module");
  if (typeof (Module as { _initPaths?: () => void })._initPaths === "function") {
    (Module as { _initPaths: () => void })._initPaths();
  }

  // Parse runtime inputs
  if (!payload.runtime_inputs || typeof payload.runtime_inputs !== "object" || Array.isArray(payload.runtime_inputs)) {
    throw new Error("worker initialize payload missing required runtime_inputs");
  }
  const rawInputs = payload.runtime_inputs as Record<string, unknown>;
  const requiredInputs = ["cwd", "home", "current_date", "package_dir"] as const;
  for (const key of requiredInputs) {
    if (typeof rawInputs[key] !== "string" || (rawInputs[key] as string).length === 0) {
      throw new Error(`worker initialize runtime_inputs missing required string ${key}`);
    }
  }
  runtimeInputs = {
    cwd: rawInputs.cwd as string,
    home: rawInputs.home as string,
    current_date: rawInputs.current_date as string,
    package_dir: rawInputs.package_dir as string,
  };

  if (typeof payload.workspace !== "string" || payload.workspace.length === 0) {
    throw new Error("worker initialize payload missing required string workspace");
  }
  workspace = payload.workspace;

  if (typeof payload.scratch !== "string" || payload.scratch.length === 0) {
    throw new Error("worker initialize payload missing required string scratch");
  }
  const scratch = payload.scratch;
  await Bun.$`mkdir -p ${scratch}`.quiet();
  process.env.PI_CODING_AGENT_DIR = scratch;
  process.env.HOME = runtimeInputs.home;

  // Pinned system-prompt.ts:666-667 renders dateTime as the UTC ISO date, which
  // is the declared current_date runtime input.
  const currentDateTime = runtimeInputs.current_date;

  // Extract length_aborted_message from pinned agent-loop.ts
  const agentLoopSource = await Bun.file(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/agent-loop.ts`).text();
  const lengthMatch = agentLoopSource.match(/reason === "length"\s*\?\s*"([^"]+)"/);
  if (!lengthMatch) {
    throw new Error("pinned agent-loop.ts lacks the length-aborted tool result message");
  }
  lengthAbortedMessage = `${lengthMatch[1]}.`;

  // Load pinned modules
  const { createAgentSession } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/sdk.ts`);
  const { Settings } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/config/settings.ts`);
  const { ModelRegistry } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/config/model-registry.ts`);
  // The pinned root is selected at runtime; use stock in-memory journaling.
  const { SessionManager } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/session/session-manager.ts`);
  const { AuthStorage } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-ai/src/auth-storage.ts`);
  const streamModule = await import(`${pinnedNodeModules}/@oh-my-pi/pi-ai/src/stream.ts`);
  streamSimple = streamModule.streamSimple;
  const validationModule = await import(`${pinnedNodeModules}/@oh-my-pi/pi-ai/src/utils/validation.ts`);
  validateToolArguments = validationModule.validateToolArguments;
  const agentLoopModule = await import(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/agent-loop.ts`);
  const normalizeTools = agentLoopModule.normalizeTools;
  abortReasonText = agentLoopModule.abortReasonText;
  // Dynamic import of runtime-selected pinned module for ToolCallBlockedError
  const runCollectorModule = await import(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/run-collector.ts`);
  ToolCallBlockedError = runCollectorModule.ToolCallBlockedError;
  const utilsModule = await import(`${pinnedNodeModules}/@oh-my-pi/pi-utils`);
  parseStreamingJson = utilsModule.parseStreamingJson;
  sanitizeText = utilsModule.sanitizeText;
  if (typeof (utilsModule as { setAgentDir?: (dir: string) => void }).setAgentDir === "function") {
    (utilsModule as { setAgentDir: (dir: string) => void }).setAgentDir(scratch);
  }

  // Settings initialization: defaults + declared overlay
  const settings = await Settings.init({
    cwd: workspace,
    agentDir: scratch,
    inMemory: true,
    configFiles: [],
    overrides: {
      "retry.enabled": false,
      "images.blockImages": true,
      "providers.streamFirstEventTimeoutSeconds": 120,
      "providers.streamIdleTimeoutSeconds": 120,
    },
  });

  const authStorage = await AuthStorage.create(":memory:");
  const modelRegistry = new ModelRegistry(authStorage, `${scratch}/models.json`, { settings });

  if (!payload.model_config || typeof payload.model_config !== "object" || Array.isArray(payload.model_config)) {
    throw new Error("worker initialize payload missing required model_config");
  }
  const modelConfig = payload.model_config as Record<string, unknown>;
  if (typeof modelConfig.id !== "string" || modelConfig.id.length === 0) {
    throw new Error("worker initialize model_config missing required string id");
  }
  if (typeof modelConfig.provider !== "string" || modelConfig.provider.length === 0) {
    throw new Error("worker initialize model_config missing required string provider");
  }
  if (typeof modelConfig.baseUrl !== "string" || modelConfig.baseUrl.length === 0) {
    throw new Error("worker initialize model_config missing required string baseUrl");
  }
  if (typeof modelConfig.api !== "string" || modelConfig.api.length === 0) {
    throw new Error("worker initialize model_config missing required string api");
  }
  if (typeof modelConfig.name !== "string" || modelConfig.name.length === 0) {
    throw new Error("worker initialize model_config missing required string name");
  }
  if (typeof modelConfig.contextWindow !== "number" || modelConfig.contextWindow <= 0) {
    throw new Error("worker initialize model_config missing required positive number contextWindow");
  }
  if (typeof modelConfig.maxTokens !== "number" || modelConfig.maxTokens <= 0) {
    throw new Error("worker initialize model_config missing required positive number maxTokens");
  }
  if (!Array.isArray(modelConfig.input)) {
    throw new Error("worker initialize model_config missing required array input");
  }

  modelRegistry.registerProvider(modelConfig.provider as string, {
    baseUrl: modelConfig.baseUrl as string,
    api: modelConfig.api as string,
    apiKey: WORKER_API_KEY,
    models: [
      {
        id: modelConfig.id as string,
        name: modelConfig.name as string,
        reasoning: Boolean(modelConfig.reasoning),
        input: modelConfig.input as string[],
        contextWindow: modelConfig.contextWindow as number,
        maxTokens: modelConfig.maxTokens as number,
        compat: modelConfig.compat,
      },
    ],
  });

  const model = modelRegistry.find(modelConfig.provider as string, modelConfig.id as string);
  if (!model) {
    throw new Error(`pinned OMP model registry could not find model: ${String(modelConfig.provider)}/${String(modelConfig.id)}`);
  }

  // Preconnect suppression: avoid opening idle sockets
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => nativeFetch(input, init)) as typeof fetch;

  const sessionManager = SessionManager.inMemory(workspace);
  const emptyJournal = sessionManager.captureState();
  const created = await createAgentSession({
    cwd: workspace,
    agentDir: scratch,
    authStorage,
    modelRegistry,
    model,
    sessionManager,
    thinkingLevel: "off",
    toolNames: [...TOOL_NAMES],
    autoApprove: false,
    skills: [],
    disableExtensionDiscovery: true,
    additionalExtensionPaths: [],
    enableMCP: false,
  });

  session = created.session;
  // SDK startup records model/settings metadata. The replay journal starts
  // with the first production message, not those non-message bootstrap events.
  sessionManager.restoreState(emptyJournal);
  journalSerial = 0;
  journalView = [];
  tools = session.agent.state.tools;
  normalizedTools = normalizeTools(tools, true);
  // Bootstrap text is the single system message the pinned provider renders
  // (openai-completions.ts:1697 joins the prompt parts with a blank line).
  const systemPrompt = pinnedSystemPrompt().join("\n\n");

  return {
    schema_version: PHASE_SCHEMA,
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: normalizedTools.map((t: unknown) => {
      const tool = t as { name: string; description: string; parameters: Record<string, unknown> };
      return {
        type: "function",
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.parameters,
        },
      };
    }),
    tools: normalizedTools.map((t: unknown) => {
      const tool = t as { name: string; description: string; parameters: Record<string, unknown> };
      return {
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      };
    }),
    bootstrap: {
      ...runtimeInputs,
      runtime_inputs: runtimeInputs,
      current_date_time: currentDateTime,
      consumer_id: "breadboard.oh-my-pi.v16.2.13",
      workspace,
      model_config: {
        ...modelConfig,
        cost: model.cost,
        contextWindow: model.contextWindow,
        maxTokens: model.maxTokens,
      },
      length_aborted_message: lengthAbortedMessage,
    },
  };
}

async function projectRequest(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!session) throw new Error("worker must be initialized before project_request");

  if (!Array.isArray(payload.messages)) {
    throw new Error("project_request payload missing required array messages");
  }

  const model = session.agent.state.model;
  // agent-loop.ts:1162 hands the session's string[] prompt to the provider as is.
  const systemPrompt = pinnedSystemPrompt();
  // agent-loop.ts:1141-1143 converts AgentMessage history before provider normalization.
  // compaction/messages.ts:190-206 turns compactionSummary into its summary user message.
  // Static imports cannot resolve the runtime-selected pinned package root.
  const { convertToLlm } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/session/messages.ts`
  );

  const context = {
    systemPrompt,
    messages: convertToLlm(payload.messages),
    tools: normalizedTools,
  };

  let captured: any = null;
  const sentinel = new Error("OMP_REQUEST_CAPTURE_SENTINEL");

  try {
    const stream = streamSimple(model, context, {
      apiKey: WORKER_API_KEY,
      onPayload: (params: any) => {
        captured = params;
        throw sentinel;
      },
    });
    for await (const _event of stream) {}
  } catch (err: unknown) {
    if (err !== sentinel && (err as { message?: string })?.message !== sentinel.message) {
      throw err;
    }
  }

  if (!captured) {
    throw new Error("project_request failed to capture params from streamSimple");
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "request",
    messages: captured.messages,
    tools: captured.tools,
    request_body: captured,
    request_members: Object.keys(captured),
  };
}

async function prepareTools(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!session) throw new Error("worker must be initialized before prepare_tools");
  if (!validateToolArguments) throw new Error("pinned validateToolArguments is unavailable");

  if (!Array.isArray(payload.calls)) {
    throw new Error("prepare_tools payload missing required array calls");
  }

  const calls = payload.calls as Array<{ id: string; name: string; arguments: unknown }>;
  preparedCalls = await Promise.all(
    calls.map(async (call) => {
      let args: unknown = call.arguments;
      if (typeof args === "string") {
        try {
          args = JSON.parse(args);
        } catch (err: unknown) {
          if (err instanceof SyntaxError) {
            args = {};
          } else {
            throw err;
          }
        }
      }
      if (!args || typeof args !== "object" || Array.isArray(args)) {
        args = {};
      }

      const item: PreparedCall = {
        id: String(call.id),
        name: String(call.name),
        arguments: args as Record<string, unknown>,
      };

      const tool = tools.find((candidate: any) => candidate.name === item.name);
      if (!tool) {
        item.error = `Tool ${item.name} not found`;
        return item;
      }

      try {
        item.arguments = validateToolArguments(tool, {
          type: "toolCall",
          id: item.id,
          name: item.name,
          arguments: args,
        });
      } catch (err: unknown) {
        item.error = err instanceof Error ? err.message : String(err);
      }

      return item;
    })
  );

  return {
    schema_version: PHASE_SCHEMA,
    kind: "prepared",
    calls: preparedCalls,
  };
}

// agent-loop.ts:220
const EMPTY_ERROR_TOOL_RESULT_TEXT = "Tool failed with no output.";

// agent-loop.ts:222-228
function hasSubstantiveToolResultContent(content: Array<{ type: string; text?: string; data?: string; mimeType?: string }>): boolean {
  for (const block of content) {
    if (block.type === "image") return true;
    if (block.type === "text" && typeof block.text === "string" && block.text.trim().length > 0) return true;
  }
  return false;
}

// agent-loop.ts:230-293
function coerceToolResult(raw: unknown): {
  result: {
    content: Array<Record<string, unknown>>;
    details: unknown;
    isError?: boolean;
    useless?: boolean;
  };
  malformed: boolean;
} {
  const rawObj = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : null;
  const rawContent = rawObj?.content;
  const details = rawObj && "details" in rawObj ? rawObj.details : {};
  // Tools may flag a non-throwing failure on the result itself (e.g. an
  // aggregator that catches per-entry errors and synthesizes a combined
  // result). Preserve the flag so agent-loop can surface it on the wire.
  const explicitError = Boolean(rawObj && "isError" in rawObj && rawObj.isError);
  // Tools may flag the result contextually useless (zero matches, elapsed
  // wait) so compaction can elide it once consumed. Errors are never useless.
  const useless = Boolean(rawObj && "useless" in rawObj && rawObj.useless);

  if (!Array.isArray(rawContent)) {
    return {
      result: {
        content: [{ type: "text", text: "Tool returned an invalid result: missing content array." }],
        details,
        isError: true,
      },
      malformed: true,
    };
  }

  const content: Array<Record<string, unknown>> = [];
  let invalidBlocks = 0;
  for (const block of rawContent) {
    if (!block || typeof block !== "object" || !("type" in block)) {
      invalidBlocks++;
      continue;
    }
    if (block.type === "text" && typeof (block as { text?: unknown }).text === "string") {
      content.push({ type: "text", text: sanitizeText((block as { text: string }).text) });
    } else if (
      block.type === "image" &&
      typeof (block as { data?: unknown }).data === "string" &&
      typeof (block as { mimeType?: unknown }).mimeType === "string"
    ) {
      content.push(block as { type: "image"; data: string; mimeType: string });
    } else {
      invalidBlocks++;
    }
  }
  if (invalidBlocks > 0) {
    content.push({
      type: "text",
      text: `Tool returned an invalid result: ${invalidBlocks} content block${invalidBlocks === 1 ? "" : "s"} had an unsupported shape.`,
    });
  }
  const isError = explicitError || invalidBlocks > 0;
  // Anthropic rejects tool_result blocks with is_error: true and empty content.
  if (isError && !hasSubstantiveToolResultContent(content)) {
    content.length = 0;
    content.push({ type: "text", text: EMPTY_ERROR_TOOL_RESULT_TEXT });
  }
  return {
    result: {
      content,
      details,
      ...(isError ? { isError: true } : {}),
      ...(useless && !isError ? { useless: true } : {}),
    },
    malformed: invalidBlocks > 0,
  };
}

// agent-loop.ts:2124-2130
function createToolSignalAbortedResult(signal: AbortSignal): {
  content: Array<Record<string, unknown>>;
  details: Record<string, unknown>;
} {
  if (!abortReasonText) throw new Error("pinned abortReasonText is unavailable");
  const reason = abortReasonText(signal);
  return {
    content: [{ type: "text", text: `Tool was not executed because the run was aborted: ${reason}.` }],
    details: {},
  };
}

async function executeBatch(_payload: Record<string, any>): Promise<Record<string, any>> {
  if (!session) throw new Error("worker must be initialized before execute_batch");

  // agent.ts:1147, 1159, 1165, 1166
  const beforeToolCall = (session.agent as { beforeToolCall?: (ctx: unknown, signal?: AbortSignal) => Promise<{ block?: boolean; reason?: string } | undefined> }).beforeToolCall;
  const afterToolCall = (session.agent as { afterToolCall?: (ctx: unknown, signal?: AbortSignal) => Promise<{ content?: Array<Record<string, unknown>>; details?: unknown; isError?: boolean; useless?: boolean } | undefined> }).afterToolCall;
  const transformToolCallArguments = (session.agent as { transformToolCallArguments?: (args: Record<string, unknown>, toolName: string) => Record<string, unknown> }).transformToolCallArguments;
  const getToolContext = (session.agent as { getToolContext?: (info: unknown) => unknown }).getToolContext;

  // agent-loop.ts:310-313
  const currentContext = {
    systemPrompt: session.agent.state.systemPrompt,
    tools: session.agent.state.tools,
    messages: session.agent.state.messages,
  };

  // The Conductor owns the transcript, so the worker session holds no assistant
  // message; executeToolCalls reads only its tool calls and timestamp from it
  // (agent-loop.ts:1659, 1662), and the prepared calls are those tool calls.
  const assistantMessage: { role: "assistant"; content: Array<Record<string, unknown>>; timestamp?: number } = {
    role: "assistant",
    content: preparedCalls.map((c) => ({
      type: "toolCall" as const,
      id: c.id,
      name: c.name,
      arguments: c.arguments,
    })),
  };

  // agent-loop.ts:1661-1662
  const toolCallInfos = preparedCalls.map((c) => ({ id: c.id, name: c.name }));
  const batchId = `${assistantMessage.timestamp ?? 0}_${preparedCalls[0]?.id ?? "batch"}`;

  // agent-loop.ts:1664-1667
  const toolSignal = new AbortController().signal;

  const results: Array<Record<string, unknown>> = [];

  for (let i = 0; i < preparedCalls.length; i++) {
    const call = preparedCalls[i];

    // agent-loop.ts:1802-1817
    if (call.error) {
      results.push({
        id: call.id,
        completion_index: i,
        content: [{ type: "text", text: call.error }],
        details: { isError: true, error: call.error },
        isError: true,
      });
      continue;
    }

    const toolCall = {
      type: "toolCall" as const,
      id: call.id,
      name: call.name,
      arguments: call.arguments,
    };

    // agent-loop.ts:1676-1678
    const tool =
      tools?.find((t: { name: string; customWireName?: string }) => t.name === call.name) ??
      tools?.find((t: { name: string; customWireName?: string }) => t.customWireName !== undefined && t.customWireName === call.name);

    // agent-loop.ts:1853-1856
    let result: {
      content: Array<Record<string, unknown>>;
      details: unknown;
      isError?: boolean;
      useless?: boolean;
    } = { content: [], details: {} };
    let isError = false;
    let completedToolExecution = false;
    let executionArgs = call.arguments;

    // agent-loop.ts:1858-1925
    try {
      // agent-loop.ts:1860
      if (!tool) throw new Error(`Tool ${call.name} not found`);
      // agent-loop.ts:1861-1865
      if (toolSignal.aborted) {
        result = createToolSignalAbortedResult(toolSignal);
        isError = true;
      } else {
        // agent-loop.ts:1867-1880
        if (beforeToolCall) {
          const beforeResult = await beforeToolCall(
            {
              assistantMessage,
              toolCall,
              args: call.arguments,
              context: currentContext,
            },
            toolSignal,
          );
          if (beforeResult?.block) {
            throw new ToolCallBlockedError(beforeResult.reason);
          }
        }
        // agent-loop.ts:1881-1885
        if (toolSignal.aborted) {
          result = createToolSignalAbortedResult(toolSignal);
          isError = true;
        } else {
          // agent-loop.ts:1886-1889
          executionArgs = transformToolCallArguments
            ? transformToolCallArguments(call.arguments, call.name)
            : call.arguments;

          // agent-loop.ts:1891-1898
          const toolContext = getToolContext
            ? getToolContext({
                batchId,
                index: i,
                total: preparedCalls.length,
                toolCalls: toolCallInfos,
              })
            : undefined;

          // agent-loop.ts:1899-1913
          const rawResult = await tool.execute(
            call.id,
            executionArgs,
            toolSignal,
            undefined,
            toolContext,
          );
          completedToolExecution = true;
          // agent-loop.ts:1915-1917
          const coerced = coerceToolResult(rawResult);
          result = coerced.result;
          if (coerced.malformed || result.isError) isError = true;
        }
      }
    } catch (e) {
      // agent-loop.ts:1918-1925
      result = {
        content: [{ type: "text", text: e instanceof Error ? e.message : String(e) }],
        details: {},
      };
      isError = true;
    }

    // agent-loop.ts:1927-1962
    if (afterToolCall && (!toolSignal.aborted || completedToolExecution)) {
      try {
        const after = await afterToolCall(
          {
            assistantMessage,
            toolCall,
            args: executionArgs,
            result,
            isError,
            context: currentContext,
          },
          toolSignal,
        );
        if (after) {
          // agent-loop.ts:1945-1952
          const coerced = coerceToolResult({
            content: after.content ?? result.content,
            details: after.details ?? result.details,
            isError: after.isError ?? result.isError,
            useless: after.useless ?? result.useless,
          });
          result = coerced.result;
          isError = coerced.malformed || (after.isError ?? isError);
        }
      } catch (e) {
        // agent-loop.ts:1954-1961
        result = {
          content: [{ type: "text", text: e instanceof Error ? e.message : String(e) }],
          details: {},
        };
        isError = true;
      }
    }

    results.push({
      id: call.id,
      completion_index: i,
      content: result.content,
      details: result.details,
      isError,
    });
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "tool_results",
    results,
  };
}

async function projectProviderFailure(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!session) throw new Error("worker must be initialized before project_provider_failure");

  if (typeof payload.http_status !== "number") {
    throw new Error("project_provider_failure payload missing required number http_status");
  }
  if (typeof payload.response_body_text !== "string") {
    throw new Error("project_provider_failure payload missing required string response_body_text");
  }
  if (!Array.isArray(payload.messages)) {
    throw new Error("project_provider_failure payload missing required array messages");
  }

  const status = payload.http_status;
  const errorBody = payload.response_body_text;
  const messages = payload.messages;

  const model = session.agent.state.model;
  const context = {
    systemPrompt: session.agent.state.systemPrompt,
    messages,
    tools: [],
  };

  // The Conductor carries only the upstream status and body; the pinned SDK
  // builds its error from those two (no status text or header is invented).
  const mockFetch = async () => new Response(errorBody, { status });

  const stream = streamSimple(model, context, {
    apiKey: WORKER_API_KEY,
    fetch: mockFetch,
  });

  let errorEvent: any = null;
  for await (const event of stream) {
    if (event.type === "error") {
      errorEvent = event;
    }
  }

  if (!errorEvent || !errorEvent.error) {
    throw new Error("pinned streamSimple did not emit an error event for provider failure");
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "provider_failure",
    message: errorEvent.error,
  };
}

function parseStreamingJsonBatch(payload: Record<string, any>): Record<string, any> {
  if (!parseStreamingJson) {
    throw new Error("pinned parseStreamingJson is unavailable");
  }
  if (!Array.isArray(payload.inputs)) {
    throw new Error("parse_streaming_json_batch payload missing required array inputs");
  }
  const inputs = payload.inputs as Array<string | null>;
  const results = inputs.map((input) => parseStreamingJson!(input === null ? undefined : input));
  return {
    schema_version: PHASE_SCHEMA,
    kind: "parsed_streaming_json_batch",
    results,
  };
}

function appendReplayEntry(timestamp: number, append: () => string): string {
  // Stock append owns topology and entry semantics (session-manager.ts:1271-1282,
  // 1333-1354). Only generated metadata is replaced at the replay boundary.
  // The journal is in-memory, so random IDs/time never reach persisted history.
  const generatedId = append();
  const id = `entry_${journalSerial++}`;
  const snapshot = session.sessionManager.captureState();
  const entries = snapshot.entries.map((entry) => entry.id === generatedId
    ? { ...entry, id, timestamp: new Date(timestamp).toISOString() }
    : entry);
  session.sessionManager.restoreState({ ...snapshot, entries });
  return id;
}

function synchronizeJournal(messages: Array<Record<string, unknown>>): void {
  if (messages.length < journalView.length) throw new Error("native history lost journal messages");
  for (let i = 0; i < journalView.length; i++) {
    if (JSON.stringify(messages[i]) !== JSON.stringify(journalView[i].message)) {
      throw new Error(`native history diverged from journal at message ${i}`);
    }
  }
  for (let i = journalView.length; i < messages.length; i++) {
    const message = messages[i];
    if (message.role === "compactionSummary") throw new Error("compaction summary has no committed journal entry");
    if (typeof message.timestamp !== "number") throw new Error("native journal message lacks replay timestamp");
    const entryId = appendReplayEntry(message.timestamp, () => session.sessionManager.appendMessage(message));
    journalView.push({ entryId, message });
  }
  session.agent.replaceMessages(messages);
}

class SentinelAbort extends Error {
  constructor(public readonly kind: string) {
    super(`SentinelAbort:${kind}`);
  }
}

type RequestEnvelope = {
  messages: Array<Record<string, unknown>>;
  max_tokens: number | null;
};

function formatRequest(
  ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> },
  options: { maxTokens?: number | null },
  model: any,
  convertMessages: (model: any, context: any, compat: any) => any[],
): RequestEnvelope {
  const wireMessages = convertMessages(model, ctx, model.compat);
  return {
    messages: wireMessages,
    max_tokens: options?.maxTokens ?? null,
  };
}

async function prepareCompactionPhase(payload: Record<string, any>): Promise<Record<string, any>> {
  if (!Array.isArray(payload?.messages)) {
    throw new Error("prepare_compaction requires messages array");
  }

  const {
    DEFAULT_COMPACTION_SETTINGS,
    compact,
    prepareCompaction,
    shouldCompact,
    calculateContextTokens,
    compactionContextTokens,
    estimateTokens,
  } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/compaction.ts`);
  const { convertMessages } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  // The package root is runtime-selected and hash-checked during initialize.
  const { computeNonMessageTokens } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/modes/utils/context-usage.ts`
  );

  if (!session?.model) {
    throw new Error("worker must be initialized with production model before prepare_compaction");
  }
  const model = session.model;
  const rawContextWindow = payload.context_window ?? session.model.contextWindow;
  if (typeof rawContextWindow !== "number" || rawContextWindow <= 0) {
    throw new Error("prepare_compaction missing valid contextWindow");
  }
  const contextWindow = rawContextWindow;
  const modelId = session.model.id;

  const settings = {
    ...DEFAULT_COMPACTION_SETTINGS,
    ...(payload.settings || {}),
  };

  let strategy = settings.strategy;
  const isVision = session.model.input?.includes("image") === true;
  if (strategy === "snapcompact" && !isVision) {
    strategy = "context-full";
  }
  if (settings.enabled === false || strategy === "off") {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "disabled",
    };
  }

  const reason = payload.reason ?? "threshold";
  synchronizeJournal(payload.messages);
  const entries = session.sessionManager.getBranch();
  if (reason === "threshold") {
    // Mid-run: agent-session.ts:9552-9562. Agent end: 9729,9743-9758.
    // Read stock-parsed usage from the native assistant, not wire usage aliases.
    const assistant = payload.messages.findLast((message: { role: string }) => message.role === "assistant");
    if (!assistant || assistant.stopReason === "aborted" || assistant.stopReason === "error") {
      return { schema_version: PHASE_SCHEMA, kind: "compaction_unavailable", reason: "not_triggered" };
    }
    if (payload.checkpoint === "before_request" && settings.midTurnEnabled === false) {
      return { schema_version: PHASE_SCHEMA, kind: "compaction_unavailable", reason: "not_triggered" };
    }
    const latestCompaction = entries.findLast((entry) => entry.type === "compaction");
    // agent-session.ts:9743-9758 discards billing retained across compaction.
    // The conductor's before_request checkpoint also follows agent_end's
    // continuation, before a new assistant has been sampled. Journal metadata
    // is authoritative here; equality is possible with the replay clock.
    const assistantPredatesCompaction = latestCompaction !== undefined &&
      assistant.timestamp <= new Date(latestCompaction.timestamp).getTime();
    const assistantUsageContextTokens = assistantPredatesCompaction ? 0 : calculateContextTokens(assistant.usage);
    // #estimateStoredContextTokens, agent-session.ts:9459-9470.
    const opts = { excludeEncryptedReasoning: true } as const;
    let messagesTokens = 0;
    for (const message of session.messages) messagesTokens += estimateTokens(message, opts);
    const storedContextTokens = computeNonMessageTokens(session) + messagesTokens;
    const contextTokens = compactionContextTokens(assistantUsageContextTokens, storedContextTokens);
    const triggerSettings = { ...settings, strategy, enabled: true };
    if (!shouldCompact(contextTokens, contextWindow, triggerSettings)) {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_unavailable",
        reason: "not_triggered",
      };
    }
  }

  const effectiveSettings = { ...settings, strategy, enabled: true };
  const preparation = prepareCompaction(entries, effectiveSettings);
  if (!preparation) {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "nothing_to_compact",
    };
  }

  const firstKeptIndex = journalView.findIndex((entry) => entry.entryId === preparation.firstKeptEntryId);
  if (firstKeptIndex === -1) throw new Error("stock retained boundary is absent from active history");

  let summaryRequest: RequestEnvelope | null = null;
  let turnPrefixRequest: RequestEnvelope | null = null;

  const replayPrepareImpl = async (_m: unknown, ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> }, options: { maxTokens?: number | null }) => {
    const hasHistory = preparation.messagesToSummarize.length > 0 || preparation.previousSummary;
    if (hasHistory && !summaryRequest) {
      summaryRequest = formatRequest(ctx, options, model, convertMessages);
      return { role: "assistant", content: [{ type: "text", text: "placeholder_summary" }], stopReason: "stop" };
    }
    if (preparation.isSplitTurn && preparation.turnPrefixMessages.length > 0 && !turnPrefixRequest) {
      turnPrefixRequest = formatRequest(ctx, options, model, convertMessages);
      return { role: "assistant", content: [{ type: "text", text: "placeholder_turn_prefix" }], stopReason: "stop" };
    }
    throw new SentinelAbort("prepare_captured");
  };
  try {
    await compact(
      preparation,
      model,
      WORKER_API_KEY,
      payload.customInstructions,
      undefined,
      { completeImpl: replayPrepareImpl },
    );
  } catch (err) {
    if (!(err instanceof SentinelAbort)) throw err;
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_prepared",
    first_kept_index: firstKeptIndex,
    preparation: {
      firstKeptIndex,
      firstKeptEntryId: preparation.firstKeptEntryId,
      isSplitTurn: preparation.isSplitTurn,
      tokensBefore: preparation.tokensBefore,
      previousSummary: preparation.previousSummary,
      previousPreserveData: preparation.previousPreserveData,
      messagesToSummarize: preparation.messagesToSummarize,
      turnPrefixMessages: preparation.turnPrefixMessages,
      recentMessages: preparation.recentMessages,
      fileOps: {
        read: Array.from(preparation.fileOps.read),
        written: Array.from(preparation.fileOps.written),
        edited: Array.from(preparation.fileOps.edited),
      },
      settings: effectiveSettings,
      modelId,
      customInstructions: payload.customInstructions,
      // Wire proof: Stock renderCompactionSummaryContext (src/compaction/messages.ts:87-89,
      // src/compaction/prompts/compaction-summary-context.md:1-5) only interpolates the summary string.
      // convertMessageToLlm (src/compaction/messages.ts:190-206) packages it into a user message, and
      // convertMessages (pi-ai/src/providers/openai-completions.ts:1612-1770) projects only
      // ChatCompletionMessageParam objects (role/content/tool_calls), completely dropping message.timestamp.
      // The replay timestamp comes from persisted native history, never wall time.
      timestamp: payload.messages[payload.messages.length - 1].timestamp,
      checkpoint: payload.checkpoint,
      entries,
      journalLeafId: session.sessionManager.getLeafId(),
      messages: payload.messages,
      reason,
      contextWindow,
    },
    summary_request: summaryRequest,
    turn_prefix_request: turnPrefixRequest,
  };
}

async function finalizeCompactionPhase(payload: Record<string, any>): Promise<Record<string, any>> {
  const prep = payload?.preparation;
  if (!prep || typeof prep !== "object") {
    throw new Error("finalize_compaction requires preparation object");
  }
  if (!session?.model) {
    throw new Error("worker must be initialized with production model before finalize_compaction");
  }
  const model = session.model;
  const rawContextWindow = prep.contextWindow ?? payload.context_window ?? session.model.contextWindow;
  if (typeof rawContextWindow !== "number" || rawContextWindow <= 0) {
    throw new Error("finalize_compaction missing valid contextWindow");
  }
  const contextWindow = rawContextWindow;

  const {
    compact,
    DEFAULT_COMPACTION_SETTINGS,
    effectiveReserveTokens,
    resolveThresholdTokens,
    compactionContextTokens,
    estimateTokens,
  } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/compaction.ts`);
  const {
    collectShakeRegions,
    applyShakeRegions,
    AGGRESSIVE_SHAKE_CONFIG,
  } = await import(`${pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/compaction/shake.ts`);
  const { computeNonMessageTokens } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/modes/utils/context-usage.ts`
  );
  const { convertMessages } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  // Dynamic import: pinnedNodeModules path is runtime-selected from discovery environment
  const autoContinuePrompt = (
    await import(
      `${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/prompts/system/auto-continue.md`,
      { with: { type: "text" } }
    )
  ).default;

  // Verbatim replica of stock agent-session.ts:10297-10304 (#buildPostCompactionEagerNudges).
  // Checks #createEagerTodoPrelude (10188-10260) and #createEagerTaskPrelude (10262-10286).
  // In this RL config, settings 'todo.eager' and 'task.eager' default to 'default', plan mode is disabled,
  // and no special preludes are active, so this produces [] identically to stock.
  const buildPostCompactionEagerNudges = (_sess: unknown, _ts: number): Array<Record<string, unknown>> => {
    const nudges: Array<Record<string, unknown>> = [];
    const sessObj = _sess && typeof _sess === "object" ? (_sess as Record<string, unknown>) : undefined;
    const settings = sessObj?.settings && typeof sessObj.settings === "object" ? (sessObj.settings as Record<string, unknown>) : undefined;
    const getSetting = typeof settings?.get === "function" ? (k: string) => (settings.get as (key: string) => unknown)(k) : undefined;
    const todoEager = getSetting?.("todo.eager");
    const todosEnabled = getSetting?.("todo.enabled");
    if (todoEager && todoEager !== "default" && todosEnabled) {
      // #createEagerTodoPrelude
    }
    const taskEager = getSetting?.("task.eager");
    if (taskEager === "always") {
      // #createEagerTaskPrelude
    }
    return nudges;
  };

  const summaries: string[] = [];
  if (typeof payload.summary === "string") summaries.push(payload.summary);
  if (typeof payload.turn_prefix_summary === "string") summaries.push(payload.turn_prefix_summary);
  if (typeof payload.short_summary === "string") summaries.push(payload.short_summary);
  else if (typeof payload.shortSummary === "string") summaries.push(payload.shortSummary);
  if (Array.isArray(payload.followup_summaries)) {
    for (const s of payload.followup_summaries) {
      if (typeof s === "string") summaries.push(s);
    }
  }

  let followupRequest: RequestEnvelope | null = null;
  let requestIndex = 0;

  const preparation = {
    firstKeptEntryId: prep.firstKeptEntryId,
    messagesToSummarize: prep.messagesToSummarize ?? [],
    turnPrefixMessages: prep.turnPrefixMessages ?? [],
    recentMessages: prep.recentMessages ?? [],
    isSplitTurn: prep.isSplitTurn ?? false,
    tokensBefore: prep.tokensBefore ?? 0,
    previousSummary: prep.previousSummary,
    previousPreserveData: prep.previousPreserveData,
    settings: prep.settings,
    fileOps: {
      read: new Set<string>(prep.fileOps?.read || []),
      written: new Set<string>(prep.fileOps?.written || []),
      edited: new Set<string>(prep.fileOps?.edited || []),
    },
  };

  const replayFinalizeImpl = async (
    _model: unknown,
    ctx: { systemPrompt?: string | string[]; messages?: Array<{ role: string; content?: unknown }> },
    options: { maxTokens?: number | null },
  ) => {
    if (requestIndex < summaries.length) {
      const summaryText = summaries[requestIndex++];
      return {
        role: "assistant",
        content: [{ type: "text", text: summaryText }],
        stopReason: "stop",
      };
    }
    followupRequest = formatRequest(ctx, options, model, convertMessages);
    throw new SentinelAbort("need_followup");
  };

  let compactResult: any = null;
  try {
    compactResult = await compact(
      preparation,
      model,
      WORKER_API_KEY,
      prep.customInstructions,
      undefined,
      { completeImpl: replayFinalizeImpl },
    );
  } catch (err) {
    if (err instanceof SentinelAbort && err.kind === "need_followup") {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_followup_request",
        request: followupRequest,
      };
    }
    throw err;
  }

  const deterministicTs = prep.timestamp;
  if (session.sessionManager.getLeafId() !== prep.journalLeafId) {
    throw new Error("compaction preparation no longer matches the persistent journal");
  }
  const compactionId = appendReplayEntry(deterministicTs, () => session.sessionManager.appendCompaction(
    compactResult.summary, compactResult.shortSummary, compactResult.firstKeptEntryId,
    compactResult.tokensBefore, compactResult.details, false, compactResult.preserveData,
  ));
  const branchEntries = session.sessionManager.getBranch();
  const compactionEntry = session.sessionManager.getEntry(compactionId);
  session.agent.replaceMessages(session.buildDisplaySessionContext().messages);
  const reason = prep.reason ?? payload.reason ?? "threshold";
  const compactionSettings = { ...DEFAULT_COMPACTION_SETTINGS, ...(prep.settings ?? {}) };
  const { createPlanReadMatcher } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/plan-mode/plan-protection.ts`
  );
  // agent-session.ts:8674-8677 (#withPlanProtection), 1387 (default reference);
  // plan-protection.ts:25-30 also protects the canonical local://PLAN.md alias.
  const planMatcher = createPlanReadMatcher(() => "local://PLAN.md");

  // Verbatim #estimateStoredContextTokens body, agent-session.ts:9459-9470.
  // `this` is the stock AgentSession; pendingMessages is empty for this replay.
  function estimateStoredContextTokens(this: typeof session, pendingMessages = []) {
    const opts = { excludeEncryptedReasoning: true } as const;
    return (
      computeNonMessageTokens(this) +
      this.messages.reduce((sum, msg) => sum + estimateTokens(msg, opts), 0) +
      pendingMessages.reduce((sum, msg) => sum + estimateTokens(msg, opts), 0)
    );
  }

  function computeResidual(sess = session): number {
    const messages = sess.buildDisplaySessionContext().messages;
    if (trailingAssistantDropped) messages.pop();
    sess.agent.replaceMessages(messages);
    // Verbatim residual expression from agent-session.ts:11132-11135,11174-11177.
    return compactionContextTokens(
      sess.getContextUsage({ contextWindow })?.tokens ?? 0,
      estimateStoredContextTokens.call(sess),
    );
  }
  function applyReplayShake(items: Parameters<typeof applyShakeRegions>[0]) {
    // shake.ts:408 stamps prunedAt. Supply the replay clock without modifying
    // stock code; dispatch is serialized, and this mutation is synchronous.
    const wallClock = Date.now;
    Date.now = () => deterministicTs;
    try {
      applyShakeRegions(items);
    } finally {
      Date.now = wallClock;
    }
  }

  let retryFits: boolean | undefined = undefined;
  let hasHeadroom = false;
  let trailingAssistantDropped = false;

  if (reason === "overflow") {
    // 1. Drop trailing error assistant (agent-session.ts:11758-11770)
    const messages = session.agent.state.messages;
    const last = messages[messages.length - 1];
    if (last?.role === "assistant" && last.stopReason === "error") {
      trailingAssistantDropped = true;
    }

    // 2. Check retry fit (agent-session.ts:11170-11183)
    let residualTokens = computeResidual(session);
    const reserveTokens = effectiveReserveTokens(contextWindow, compactionSettings);
    const defaultReserveTokens = Math.floor(contextWindow * 0.15);
    const fitReserveTokens = Math.min(reserveTokens, defaultReserveTokens);
    const fitBudget = Math.max(0, contextWindow - fitReserveTokens);
    retryFits = residualTokens <= fitBudget;

    // 3. Shake rescue if dead-end (agent-session.ts:11778-11784, 11207-11218, 8806-8855)
    if (!retryFits) {
      const config = {
        ...AGGRESSIVE_SHAKE_CONFIG,
        keepBoundaryId: compactionEntry.firstKeptEntryId,
        protectedTools: [...AGGRESSIVE_SHAKE_CONFIG.protectedTools, planMatcher],
      };
      const regions = collectShakeRegions(branchEntries, config);
      if (regions.length > 0) {
        // agent-session.ts:8824, 8870-8881 (#saveShakeArtifact)
        const parts: string[] = [];
        for (let i = 0; i < regions.length; i++) {
          const region = regions[i];
          parts.push(`### region ${i + 1} (${region.label}, ~${region.tokens} tok)`, "", region.originalText, "");
        }
        let artifactId: string | undefined = undefined;
        try {
          artifactId = await session.sessionManager.saveArtifact(parts.join("\n"), "shake");
        } catch {}

        // agent-session.ts:8825, 8857-8862 (#shakeElidePlaceholder)
        const replacements = regions.map((region: any, index: number) => {
          if (artifactId) {
            return `[shaken ~${region.tokens} tokens — recover: artifact://${artifactId} (region ${index + 1})]`;
          }
          return `[shaken ~${region.tokens} tokens]`;
        });

        // agent-session.ts:8831-8840
        const items = regions.map((region: any, index: number) => ({
          region,
          replacement: replacements[index],
        }));
        applyReplayShake(items);


        residualTokens = computeResidual(session);
        retryFits = residualTokens <= fitBudget;
      }
    }
  } else if (reason === "threshold") {
    // 1. Check headroom (agent-session.ts:11128-11147)
    // agent-session.ts:480 (COMPACTION_RECOVERY_BAND = 0.8)
    const COMPACTION_RECOVERY_BAND = 0.8;
    let residualTokens = computeResidual(session);
    const thresholdTokens = resolveThresholdTokens(contextWindow, compactionSettings);
    const recoveryBand = Math.floor(thresholdTokens * COMPACTION_RECOVERY_BAND);
    hasHeadroom = residualTokens <= recoveryBand;

    // 2. Shake rescue if no headroom (agent-session.ts:11800-11806, 11207-11218, 8806-8855)
    if (!hasHeadroom) {
      const config = {
        ...AGGRESSIVE_SHAKE_CONFIG,
        keepBoundaryId: compactionEntry.firstKeptEntryId,
        protectedTools: [...AGGRESSIVE_SHAKE_CONFIG.protectedTools, planMatcher],
      };
      const regions = collectShakeRegions(branchEntries, config);
      if (regions.length > 0) {
        const parts: string[] = [];
        for (let i = 0; i < regions.length; i++) {
          const region = regions[i];
          parts.push(`### region ${i + 1} (${region.label}, ~${region.tokens} tok)`, "", region.originalText, "");
        }
        let artifactId: string | undefined = undefined;
        try {
          artifactId = await session.sessionManager.saveArtifact(parts.join("\n"), "shake");
        } catch {}

        const replacements = regions.map((region: any, index: number) => {
          if (artifactId) {
            return `[shaken ~${region.tokens} tokens — recover: artifact://${artifactId} (region ${index + 1})]`;
          }
          return `[shaken ~${region.tokens} tokens]`;
        });

        const items = regions.map((region: any, index: number) => ({
          region,
          replacement: replacements[index],
        }));
        applyReplayShake(items);


        residualTokens = computeResidual(session);
        hasHeadroom = residualTokens <= recoveryBand;
      }
    }
  }

  const compactionMessage = {
    role: "compactionSummary",
    summary: compactResult.summary,
    shortSummary: compactResult.shortSummary,
    firstKeptEntryId: compactResult.firstKeptEntryId,
    tokensBefore: compactResult.tokensBefore,
    details: compactResult.details,
    timestamp: deterministicTs,
  };

  const res: Record<string, any> = {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_finalized",
    compaction_message: compactionMessage,
    compaction_entry: compactionMessage,
    first_kept_index: prep.firstKeptIndex,
    summary: compactResult.summary,
    ...(retryFits !== undefined ? { retry: retryFits } : {}),
  };
  const willRetry = reason === "overflow" || reason === "incomplete";
  // compaction.autoContinue defaults to true (src/config/settings-schema.ts:2004);
  // stock reads it from the compaction settings group (agent-session.ts:11262).
  const effectiveAutoContinue = prep.settings.autoContinue !== false;
  const checkpoint = prep.checkpoint;

  // Context-full path, agent-session.ts:11791-11815: only schedule the
  // auto-continue prompt when the rebuilt context has recovery-band headroom.
  if (
    checkpoint === "agent_end" &&
    reason === "threshold" &&
    !willRetry &&
    hasHeadroom &&
    effectiveAutoContinue
  ) {
    const eagerNudges = buildPostCompactionEagerNudges(session, deterministicTs);
    const continueTs = deterministicTs + eagerNudges.length + 1;
    const autoContinueMessage = {
      role: "developer",
      content: [{ type: "text", text: autoContinuePrompt }],
      attribution: "agent",
      timestamp: continueTs,
    };
    res.continuation = [...eagerNudges, autoContinueMessage];
  }

  // Rebuild from stock's chronological journal, never from the display view.
  const messages = [compactionMessage, ...session.agent.state.messages.slice(1)];
  res.messages = messages;
  journalView = messages.map((message, index) => {
    if (index === 0) return { entryId: compactionId, message };
    const entry = branchEntries.find((candidate) => candidate.type === "message" && candidate.message === message);
    if (!entry) throw new Error("stock display message has no journal entry");
    return { entryId: entry.id, message };
  });
  return res;
}
async function close(): Promise<Record<string, any>> {
  const owned = await descendantHandles(process.pid);
  let disposeError: unknown;
  if (session) {
    try {
      await session.dispose();
    } catch (err: unknown) {
      disposeError = err;
    }
    session = null;
    tools = [];
    normalizedTools = [];
  }
  const processes = await reapDescendants(owned);
  if (disposeError) throw disposeError;
  return {
    schema_version: PHASE_SCHEMA,
    kind: "closed",
    cleanup: { processes, all_dead: processes.length === 0 },
  };
}

async function parseProviderUsage(payload: { usage: object | null }) {
  if (!session) throw new Error("worker must be initialized before parse_provider_usage");
  // Runtime-selected pinned modules. Stock stream parser: openai-completions.ts:1011-1013,1533-1578.
  const { parseChunkUsage } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-completions.ts`
  );
  const { createInitialResponsesAssistantMessage } = await import(
    `${pinnedNodeModules}/@oh-my-pi/pi-ai/src/providers/openai-shared.ts`
  );
  const model = session.model;
  // No-usage stream: openai-completions.ts:565. The factory's timestamp is
  // discarded; only its stock initial usage crosses this deterministic seam.
  const usage = payload.usage === null
    ? createInitialResponsesAssistantMessage(model.api, model.provider, model.id).usage
    : parseChunkUsage(payload.usage, model, undefined);
  return { schema_version: PHASE_SCHEMA, kind: "assistant_usage", usage };
}

async function dispatch(operation: string, payload: Record<string, any>): Promise<Record<string, any>> {
  switch (operation) {
    case "initialize":
      return initialize(payload);
    case "project_request":
      return projectRequest(payload);
    case "prepare_tools":
      return prepareTools(payload);
    case "execute_batch":
      return executeBatch(payload);
    case "project_provider_failure":
      return projectProviderFailure(payload);
    case "parse_streaming_json_batch":
      return parseStreamingJsonBatch(payload);
    case "parse_provider_usage":
      return parseProviderUsage(payload);
    case "prepare_compaction":
      return prepareCompactionPhase(payload);
    case "finalize_compaction":
      return finalizeCompactionPhase(payload);
    case "close":
      return close();
    default:
      throw new Error(`unknown OMP 16.2.13 phase: ${operation}`);
  }
}

// Framed Bun stdin/stdout loop with 4-byte BE uint32 length prefix
const decoder = new TextDecoder();
const reader = Bun.stdin.stream().getReader();
let buffer = new Uint8Array(0);

function append(chunk: Uint8Array) {
  const next = new Uint8Array(buffer.length + chunk.length);
  next.set(buffer);
  next.set(chunk, buffer.length);
  buffer = next;
}

async function writeFrame(value: Record<string, any>) {
  const encoded = new TextEncoder().encode(JSON.stringify(value));
  const frame = new Uint8Array(4 + encoded.length);
  new DataView(frame.buffer).setUint32(0, encoded.length, false);
  frame.set(encoded, 4);
  await new Promise<void>((resolve, reject) => {
    process.stdout.write(frame, (error) => error ? reject(error) : resolve());
  });
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
    if (payload.schema_version !== RPC_SCHEMA || typeof payload.request_id !== "number") {
      throw new Error("invalid worker frame");
    }
    try {
      const result = await dispatch(payload.operation, payload.payload);
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, result });
    } catch (error: unknown) {
      await writeFrame({
        schema_version: RPC_SCHEMA,
        request_id: payload.request_id,
        error: { type: "WorkerPhaseError", message: error instanceof Error ? error.message : String(error) },
      });
    }
  }
}
