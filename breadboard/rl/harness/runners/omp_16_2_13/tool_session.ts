import { readdir, readFile } from "node:fs/promises";
import { replayState } from "./compaction_replay.ts";




class ToolSessionState {
  pinnedNodeModules = "";
  workspace = "";
  runtimeInputs: Record<string, string> = {};
  session: any = null;
  tools: any[] = [];
  normalizedTools: any[] = [];
  validateToolArguments: ((tool: any, call: any) => any) | null = null;
  streamSimple: any = null;
  parseStreamingJson: ((input: string | undefined) => any) | null = null;
  preparedCalls: PreparedCall[] = [];
  lengthAbortedMessage = "";
  sanitizeText: (text: string) => string = (t) => t;
  abortReasonText: ((signal: AbortSignal | undefined) => string) | null = null;
  ToolCallBlockedError: new (reason?: string) => Error = Error;
}
const sessionState = new ToolSessionState();
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
  const prompt = sessionState.session.agent.state.systemPrompt;
  if (!Array.isArray(prompt) || !prompt.every((part: unknown) => typeof part === "string")) {
    throw new Error("pinned agent session system prompt is not string[]");
  }
  return prompt;
}

async function initialize(payload: Record<string, unknown>) {
  // The sandbox launch environment (sandbox.py native phase environment) owns the root.
  const nodeModulesRoot = process.env.OMP16213_CODING_AGENT_NODE_MODULES;
  if (typeof nodeModulesRoot !== "string" || nodeModulesRoot.length === 0) {
    throw new Error("initialize requires OMP16213_CODING_AGENT_NODE_MODULES");
  }
  sessionState.pinnedNodeModules = nodeModulesRoot.replace(/\/+$/, "");

  // Fail-closed verification of pinned files
  for (const [relativePath, expectedSha] of Object.entries(PINNED_SHA256)) {
    const filePath = `${sessionState.pinnedNodeModules}/${relativePath}`;
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
  process.env.NODE_PATH = sessionState.pinnedNodeModules;
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
  sessionState.runtimeInputs = {
    cwd: rawInputs.cwd as string,
    home: rawInputs.home as string,
    current_date: rawInputs.current_date as string,
    package_dir: rawInputs.package_dir as string,
  };

  if (typeof payload.workspace !== "string" || payload.workspace.length === 0) {
    throw new Error("worker initialize payload missing required string workspace");
  }
  sessionState.workspace = payload.workspace;

  if (typeof payload.scratch !== "string" || payload.scratch.length === 0) {
    throw new Error("worker initialize payload missing required string scratch");
  }
  const scratch = payload.scratch;
  await Bun.$`mkdir -p ${scratch}`.quiet();
  process.env.PI_CODING_AGENT_DIR = scratch;
  process.env.HOME = sessionState.runtimeInputs.home;

  // Pinned system-prompt.ts:666-667 renders dateTime as the UTC ISO date, which
  // is the declared current_date runtime input.
  const currentDateTime = sessionState.runtimeInputs.current_date;

  // Extract length_aborted_message from pinned agent-loop.ts
  const agentLoopSource = await Bun.file(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/agent-loop.ts`).text();
  const lengthMatch = agentLoopSource.match(/reason === "length"\s*\?\s*"([^"]+)"/);
  if (!lengthMatch) {
    throw new Error("pinned agent-loop.ts lacks the length-aborted tool result message");
  }
  sessionState.lengthAbortedMessage = `${lengthMatch[1]}.`;

  // Load pinned modules
  const { createAgentSession } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/sdk.ts`);
  const { Settings } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/config/settings.ts`);
  const { ModelRegistry } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/config/model-registry.ts`);
  // The pinned root is selected at runtime; use stock in-memory journaling.
  const { SessionManager } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-coding-agent/src/session/session-manager.ts`);
  const { AuthStorage } = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/auth-storage.ts`);
  const streamModule = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/stream.ts`);
  sessionState.streamSimple = streamModule.streamSimple;
  const validationModule = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-ai/src/utils/validation.ts`);
  sessionState.validateToolArguments = validationModule.validateToolArguments;
  const agentLoopModule = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/agent-loop.ts`);
  const normalizeTools = agentLoopModule.normalizeTools;
  sessionState.abortReasonText = agentLoopModule.abortReasonText;
  // Dynamic import of runtime-selected pinned module for ToolCallBlockedError
  const runCollectorModule = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-agent-core/src/run-collector.ts`);
  sessionState.ToolCallBlockedError = runCollectorModule.ToolCallBlockedError;
  const utilsModule = await import(`${sessionState.pinnedNodeModules}/@oh-my-pi/pi-utils`);
  sessionState.parseStreamingJson = utilsModule.parseStreamingJson;
  sessionState.sanitizeText = utilsModule.sanitizeText;
  if (typeof (utilsModule as { setAgentDir?: (dir: string) => void }).setAgentDir === "function") {
    (utilsModule as { setAgentDir: (dir: string) => void }).setAgentDir(scratch);
  }

  // Settings initialization: defaults + declared overlay
  const settings = await Settings.init({
    cwd: sessionState.workspace,
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

  const sessionManager = SessionManager.inMemory(sessionState.workspace);
  const emptyJournal = sessionManager.captureState();
  const created = await createAgentSession({
    cwd: sessionState.workspace,
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

  sessionState.session = created.session;
  // SDK startup records model/settings metadata. The replay journal starts
  // with the first production message, not those non-message bootstrap events.
  sessionManager.restoreState(emptyJournal);
  replayState.journalSerial = 0;
  replayState.journalView = [];
  sessionState.tools = sessionState.session.agent.state.tools;
  sessionState.normalizedTools = normalizeTools(sessionState.tools, true);
  // Bootstrap text is the single system message the pinned provider renders
  // (openai-completions.ts:1697 joins the prompt parts with a blank line).
  const systemPrompt = pinnedSystemPrompt().join("\n\n");

  return {
    schema_version: PHASE_SCHEMA,
    kind: "initialized",
    system_prompt: systemPrompt,
    tool_schemas: sessionState.normalizedTools.map((t: unknown) => {
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
    tools: sessionState.normalizedTools.map((t: unknown) => {
      const tool = t as { name: string; description: string; parameters: Record<string, unknown> };
      return {
        name: tool.name,
        description: tool.description,
        parameters: tool.parameters,
      };
    }),
    bootstrap: {
      ...sessionState.runtimeInputs,
      runtime_inputs: sessionState.runtimeInputs,
      current_date_time: currentDateTime,
      consumer_id: "breadboard.oh-my-pi.v16.2.13",
      workspace: sessionState.workspace,
      model_config: {
        ...modelConfig,
        cost: model.cost,
        contextWindow: model.contextWindow,
        maxTokens: model.maxTokens,
      },
      length_aborted_message: sessionState.lengthAbortedMessage,
    },
  };
}


async function prepareTools(payload: Record<string, unknown>) {
  if (!sessionState.session) throw new Error("worker must be initialized before prepare_tools");
  if (!sessionState.validateToolArguments) throw new Error("pinned validateToolArguments is unavailable");

  if (!Array.isArray(payload.calls)) {
    throw new Error("prepare_tools payload missing required array calls");
  }

  const calls = payload.calls as Array<{ id: string; name: string; arguments: unknown }>;
  sessionState.preparedCalls = await Promise.all(
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

      const tool = sessionState.tools.find((candidate: any) => candidate.name === item.name);
      if (!tool) {
        item.error = `Tool ${item.name} not found`;
        return item;
      }

      try {
        item.arguments = sessionState.validateToolArguments(tool, {
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
    calls: sessionState.preparedCalls,
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
      content.push({ type: "text", text: sessionState.sanitizeText((block as { text: string }).text) });
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
  if (!sessionState.abortReasonText) throw new Error("pinned abortReasonText is unavailable");
  const reason = sessionState.abortReasonText(signal);
  return {
    content: [{ type: "text", text: `Tool was not executed because the run was aborted: ${reason}.` }],
    details: {},
  };
}

async function executeBatch(_payload: Record<string, unknown>) {
  if (!sessionState.session) throw new Error("worker must be initialized before execute_batch");

  // agent.ts:1147, 1159, 1165, 1166
  const beforeToolCall = (sessionState.session.agent as { beforeToolCall?: (ctx: unknown, signal?: AbortSignal) => Promise<{ block?: boolean; reason?: string } | undefined> }).beforeToolCall;
  const afterToolCall = (sessionState.session.agent as { afterToolCall?: (ctx: unknown, signal?: AbortSignal) => Promise<{ content?: Array<Record<string, unknown>>; details?: unknown; isError?: boolean; useless?: boolean } | undefined> }).afterToolCall;
  const transformToolCallArguments = (sessionState.session.agent as { transformToolCallArguments?: (args: Record<string, unknown>, toolName: string) => Record<string, unknown> }).transformToolCallArguments;
  const getToolContext = (sessionState.session.agent as { getToolContext?: (info: unknown) => unknown }).getToolContext;

  // agent-loop.ts:310-313
  const currentContext = {
    systemPrompt: sessionState.session.agent.state.systemPrompt,
    tools: sessionState.session.agent.state.tools,
    messages: sessionState.session.agent.state.messages,
  };

  // The Conductor owns the transcript, so the worker session holds no assistant
  // message; executeToolCalls reads only its tool calls and timestamp from it
  // (agent-loop.ts:1659, 1662), and the prepared calls are those tool calls.
  const assistantMessage: { role: "assistant"; content: Array<Record<string, unknown>>; timestamp?: number } = {
    role: "assistant",
    content: sessionState.preparedCalls.map((c) => ({
      type: "toolCall" as const,
      id: c.id,
      name: c.name,
      arguments: c.arguments,
    })),
  };

  // agent-loop.ts:1661-1662
  const toolCallInfos = sessionState.preparedCalls.map((c) => ({ id: c.id, name: c.name }));
  const batchId = `${assistantMessage.timestamp ?? 0}_${sessionState.preparedCalls[0]?.id ?? "batch"}`;

  // agent-loop.ts:1664-1667
  const toolSignal = new AbortController().signal;

  const results: Array<Record<string, unknown>> = [];

  for (let i = 0; i < sessionState.preparedCalls.length; i++) {
    const call = sessionState.preparedCalls[i];

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
      sessionState.tools?.find((t: { name: string; customWireName?: string }) => t.name === call.name) ??
      sessionState.tools?.find((t: { name: string; customWireName?: string }) => t.customWireName !== undefined && t.customWireName === call.name);

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
            throw new sessionState.ToolCallBlockedError(beforeResult.reason);
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
                total: sessionState.preparedCalls.length,
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


async function close(): Promise<Record<string, any>> {
  const owned = await descendantHandles(process.pid);
  let disposeError: unknown;
  if (sessionState.session) {
    try {
      await sessionState.session.dispose();
    } catch (err: unknown) {
      disposeError = err;
    }
    sessionState.session = null;
    sessionState.tools = [];
    sessionState.normalizedTools = [];
  }
  const processes = await reapDescendants(owned);
  if (disposeError) throw disposeError;
  return {
    schema_version: PHASE_SCHEMA,
    kind: "closed",
    cleanup: { processes, all_dead: processes.length === 0 },
  };
}




export { PHASE_SCHEMA, WORKER_API_KEY, close, executeBatch, initialize, pinnedSystemPrompt, prepareTools, sessionState };
