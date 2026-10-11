import { readdir, readFile, realpath } from "node:fs/promises";
import { withJournalMetadata } from "./compaction_replay.ts";




class ToolSessionState {
  pinnedSourceRoot = "";
  boundedDescriptions: Record<string, string> = {};
  nativeSystemPrompt: string[] = [];
  capabilityDenials: Record<string, Record<string, unknown>> = {};
  session: any = null;
  tools: any[] = [];
  irToJsonSchema: ((ir: unknown, options?: Record<string, unknown>) => Record<string, unknown>) | null = null;
  validateToolArguments: ((tool: unknown, call: { type: "toolCall"; id: string; name: string; arguments: unknown }) => Record<string, unknown>) | null = null;
  workspace = "";
  runtimeInputs: Record<string, string> = {};
  currentDate = "";
  convertMessages: ((model: any, context: any, compat: any) => unknown[]) | null = null;
  reminderInjector: { transform: (context: { systemPrompt: string[]; messages: unknown[] }, date: string, cwd: string) => unknown } | null = null;
  workerCompactionEnabled = false;
  compactionPkg: any = null;
  compactionUtils: any = null;
  compactionMethods: any = null;
  shakePkg: any = null;
  tokenizerPkg: any = null;
  queuedMessagesPkg: { isTerminalTextAssistantAnswer: (message: unknown) => boolean };
  TodoTracker: new (host: unknown) => { buildPostCompactionEagerNudges: () => Array<Record<string, unknown>> };
  parseChunkUsage: (usage: object, model: unknown, premiumRequests: undefined, timestamp: number) => unknown;
  createInitialAssistantMessage: (api: string, provider: string, model: string) => { usage: unknown };
  summarizationSystemPromptText = "";
  autoContinuePromptText = "";
  convertToLlm: (messages: unknown[]) => unknown[];
  computeNonMessageTokensFn: (session: unknown, tokenizer: unknown) => number;
  streamOpenAICompletions: (
  model: unknown,
  context: { systemPrompt: string[]; messages: unknown[]; tools: unknown[] },
  options: { apiKey: string; fetch: typeof fetch; maxRetries: number },
) => AsyncIterable<{ type: string; error?: Record<string, unknown> }>;
  prepared: Array<Call & { error?: string }> = [];
  pinnedReadTool: ReadRouteTool | null = null;
}
const sessionState = new ToolSessionState();
const PHASE_SCHEMA = "bb.omp-native.v1";
const TOOL_NAMES = ["read", "bash", "edit", "write"] as const;
type Call = { id: string; name: string; arguments: Record<string, unknown> };
type RouteClassifier = { sourceRoot: string; lockSha256: string; moduleDigests: Map<string, string> };










// Stock renders the process-local calendar date of the request instant
// (sdk.ts:3460-3463, :4152-4156 via utils/local-date.ts:2-7). The admitted
// message_timestamp_ms is that instant; the date is observed in this process.

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
  if (process.platform === "darwin") {
    const child = Bun.spawn(["/bin/ps", "-axo", "pid=,ppid=,pgid=,stat="], {
      stdout: "pipe", stderr: "pipe",
    });
    const [output, error, status] = await Promise.all([
      new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited,
    ]);
    if (status !== 0) throw new Error(`process table inspection failed: ${error}`);
    for (const row of output.split("\n")) {
      if (!row.trim()) continue;
      const match = row.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s*$/);
      if (!match) throw new Error(`invalid process table row: ${row}`);
      const pid = Number(match[1]);
      const ppid = Number(match[2]);
      const pgid = Number(match[3]);
      if (!Number.isSafeInteger(pid) || pid <= 0 || !Number.isSafeInteger(ppid)
        || !Number.isSafeInteger(pgid) || pgid <= 0 || table.has(pid)) {
        throw new Error(`invalid process table identity: ${row}`);
      }
      table.set(pid, { pid, ppid, pgid, state: match[4] });
    }
    if (!table.has(process.pid)) throw new Error("process table omitted worker identity");
    return table;
  }
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

function requireRuntimeInputs(value: unknown): Record<string, string> {
  const names = ["cwd", "home", "message_timestamp_ms", "package_dir"];
  const input = exactRecord(value, "runtime_inputs", names);
  const typed: Record<string, string> = {};
  for (const name of names) {
    if (typeof input[name] !== "string" || !input[name]) {
      throw new Error(`runtime_inputs.${name} must be a non-empty string`);
    }
    typed[name] = input[name] as string;
  }
  if (!/^\d{13}$/.test(typed.message_timestamp_ms) || !Number.isSafeInteger(Number(typed.message_timestamp_ms))) {
    throw new Error("runtime_inputs.message_timestamp_ms must be a 13-digit epoch millisecond string");
  }
  return typed;
}

function digestHex(value: unknown, label: string): string {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} must be a sha256 digest`);
  }
  return value.slice("sha256:".length);
}

const ROUTE_CLASSIFIER_MODULE_NAMES = [
  "path-utils.ts",
  "read-path-resolution.ts",
  "read-archive.ts",
  "read-sqlite.ts",
  "read-pdf.ts",
  "video.ts",
  "mime.ts",
  "markit.ts",
  "router.ts",
] as const;

function requireRouteClassifier(value: unknown): RouteClassifier {
  const classifier = exactRecord(
    value,
    "route_classifier",
    ["schema_version", "source_root", "source_archive_sha256", "lock_sha256", "modules"],
  );
  if (classifier.schema_version !== "bb.omp-route-classifier.v1" || typeof classifier.source_root !== "string" || !classifier.source_root) {
    throw new Error("route_classifier has invalid schema or source_root");
  }
  digestHex(classifier.source_archive_sha256, "route_classifier.source_archive_sha256");
  const rawModules = exactRecord(classifier.modules, "route_classifier.modules", ROUTE_CLASSIFIER_MODULE_NAMES);
  const moduleDigests = new Map<string, string>();
  for (const name of ROUTE_CLASSIFIER_MODULE_NAMES) {
    const module = exactRecord(rawModules[name], `route_classifier.modules.${name}`, ["path", "sha256"]);
    if (typeof module.path !== "string" || !module.path || module.path.startsWith("/") || module.path.includes("..")) {
      throw new Error(`route_classifier.modules.${name}.path is invalid`);
    }
    moduleDigests.set(module.path, digestHex(module.sha256, `route_classifier.modules.${name}.sha256`));
  }
  return {
    sourceRoot: classifier.source_root,
    lockSha256: digestHex(classifier.lock_sha256, "route_classifier.lock_sha256"),
    moduleDigests,
  };
}

type PinnedRoute = { route: string; matched_path?: string };

function routeCapability(route: string): string | undefined {
  if (
    route === "archive" || route === "sqlite" || route === "image" || route === "video"
    || route === "pdf" || route === "document" || route === "url" || route === "ssh"
  ) {
    return route;
  }
  if (route.startsWith("internal:")) return "internal-resource";
  return undefined;
}

const PINNED_READ_SHA256 = "270694388f57680524c3df3f6223e845dc8c4d78e2146748d2013c63ae9ba935";
const READ_ROUTE_MARKER = "__OMP_PINNED_ROUTE__:";
const READ_BRANCHES: ReadonlyArray<readonly [string, string]> = [
  ["if (parsedUrlTarget) {", "url"],
  ["return this.#handleInternalUrl(internalTarget.path, parsed, signal);", "internal"],
  ["if (archivePath) {", "archive"],
  ["if (sqlitePath) {", "sqlite"],
  ["if (isDirectory) {", "file"],
  ["if (pdfImageRead) {", "pdf"],
  ["if (isVideoPath(absolutePath)) {", "video"],
  ["if (parsed.kind === \"image\") {", "image"],
  ["} else if (mimeType) {", "image"],
  ["} else if (shouldConvertWithMarkit) {", "document"],
  ["// One read for every consumer below.", "file"],
  ["throw new ToolError(`Path '${localReadPath}' not found`);", "file"],
];
type ReadRouteTool = { execute: (id: string, params: { path: string }) => Promise<unknown> };


function observePinnedReadBranches(sourceRoot: string): void {
  const sourcePath = `${sourceRoot}/packages/coding-agent/src/tools/read.ts`;
  Bun.plugin({
    name: "verified-omp-read-route-observer",
    setup(build) {
      build.onLoad({ filter: /\/tools\/read\.ts\?omp-route-observer$/ }, async ({ path }) => {
        const canonicalSource = await realpath(sourcePath);
        if (path !== `${canonicalSource}?omp-route-observer`) {
          throw new Error(`unexpected route observer module path ${path}`);
        }
        if (await sha256File(sourcePath) !== PINNED_READ_SHA256) throw new Error("pinned read dispatcher digest mismatch");
        let contents = await Bun.file(sourcePath).text();
        for (const [needle, route] of READ_BRANCHES) {
          const occurrences = contents.split(needle).length - 1;
          if (occurrences !== (route === "internal" ? 2 : 1)) {
            throw new Error(`pinned read branch changed: ${needle}`);
          }
          const observation = route === "internal"
            ? `throw new Error("${READ_ROUTE_MARKER}" + (scheme === "ssh" ? "ssh" : "internal"));`
            : `throw new Error("${READ_ROUTE_MARKER}${route}");`;
          contents = contents.replaceAll(needle, needle.endsWith("{") ? `${needle}\n${observation}` : `${observation}\n${needle}`);
        }
        return { contents, loader: "ts" };
      });
    },
  });
}

async function classifyPinnedRead(value: unknown): Promise<PinnedRoute> {
  if (typeof value !== "string" || sessionState.pinnedReadTool === null) return { route: "unclassified" };
  try {
    await sessionState.pinnedReadTool.execute("read-route-observation", { path: value });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.startsWith(READ_ROUTE_MARKER)) {
      return { route: message.slice(READ_ROUTE_MARKER.length) };
    }
  }
  return { route: "unclassified" };
}
function requireAdvertisement(value: unknown): {
  descriptionPolicy: Record<string, Record<string, unknown>>;
  capabilityDenials: Record<string, Record<string, unknown>>;
  providerId: string;
} {
  const advertisement = exactRecord(value, "advertisement", ["bounded_description_policy", "capability_denials", "model_registry"], ["settings"]);
  const registry = exactRecord(advertisement.model_registry, "advertisement.model_registry", ["provider_id"]);
  if (typeof registry.provider_id !== "string" || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(registry.provider_id)) {
    throw new Error("advertisement.model_registry.provider_id must be a route label token");
  }
  const rawPolicy = exactRecord(advertisement.bounded_description_policy, "advertisement.bounded_description_policy", TOOL_NAMES);
  const descriptionPolicy: Record<string, Record<string, unknown>> = {};
  for (const name of TOOL_NAMES) {
    const entry = exactRecord(rawPolicy[name], `advertisement.bounded_description_policy.${name}`, ["original_sha256", "bounded_sha256", "removed_spans"]);
    for (const field of ["original_sha256", "bounded_sha256"]) digestHex(entry[field], `${name}.${field}`);
    if (!Array.isArray(entry.removed_spans)) throw new Error(`${name}.removed_spans must be an array`);
    for (const span of entry.removed_spans) {
      const source = exactRecord(span, `${name}.removed_spans`, ["text", "source_file"]);
      if (typeof source.text !== "string" || !source.text || typeof source.source_file !== "string") throw new Error(`${name}.removed_spans is invalid`);
    }
    descriptionPolicy[name] = entry;
  }
  const rawDenials = advertisement.capability_denials;
  if (!rawDenials || typeof rawDenials !== "object" || Array.isArray(rawDenials)) {
    throw new Error("advertisement.capability_denials must be an object");
  }
  const capabilityDenials: Record<string, Record<string, unknown>> = {};
  for (const [capability, value] of Object.entries(rawDenials as Record<string, unknown>)) {
    const entry = exactRecord(
      value,
      `advertisement.capability_denials.${capability}`,
      ["schema_version", "capability", "message", "source_ref"],
      ["route"],
    );
    if (
      entry.schema_version !== "bb.omp-capability-denial.v1"
      || entry.capability !== capability
      || typeof entry.message !== "string"
      || typeof entry.source_ref !== "string"
      || (capability !== "pty" && capability !== "async"
        && (!entry.route || typeof entry.route !== "object" || Array.isArray(entry.route)))
    ) throw new Error(`advertisement has invalid capability denial: ${capability}`);
    capabilityDenials[capability] = entry;
  }
  for (const capability of ["pty", "async"]) {
    if (!capabilityDenials[capability]) {
      throw new Error(`advertisement is missing capability denial: ${capability}`);
    }
  }
  if (advertisement.settings !== undefined) {

    exactRecord(advertisement.settings, "advertisement.settings", ["request_cap", "model_max_tokens", "provider_attempts"]);
  }
  return { descriptionPolicy, capabilityDenials, providerId: registry.provider_id };
}

// The lease binds the route and model. Its provider names the wire protocol
// family and its compat is not consumed: pinned resolveModelPolicy derives
// compat from the registered provider id and route.
function requireLeaseModel(value: unknown): { baseUrl: string; definition: Record<string, unknown> } {
  const model = exactRecord(
    value,
    "model_config",
    ["id", "name", "api", "baseUrl", "reasoning", "input", "contextWindow", "maxTokens"],
    ["provider", "compat"],
  );
  if (
    typeof model.id !== "string" || !model.id
    || typeof model.name !== "string" || !model.name
    || model.api !== "openai-completions"
    || typeof model.baseUrl !== "string" || !model.baseUrl
    || typeof model.reasoning !== "boolean"
    || !Array.isArray(model.input) || model.input.some((entry) => typeof entry !== "string")
    || !Number.isSafeInteger(model.contextWindow) || (model.contextWindow as number) <= 0
    || !Number.isSafeInteger(model.maxTokens) || (model.maxTokens as number) <= 0
  ) throw new Error("model_config is not a bound OpenAI Completions lease model");
  return {
    baseUrl: model.baseUrl as string,
    definition: {
      id: model.id, name: model.name, reasoning: model.reasoning, input: model.input,
      contextWindow: model.contextWindow, maxTokens: model.maxTokens,
    },
  };
}

function deniedCapability(argumentsValue: Record<string, unknown>): string | undefined {
  for (const capability of ["pty", "async"]) {
    if (argumentsValue[capability] !== true) continue;
    const entry = sessionState.capabilityDenials[capability];
    if (!entry || entry.capability !== capability || typeof entry.message !== "string") {
      return `OMP capability denial policy unavailable: ${capability}`;
    }
    return entry.message;
  }
  return undefined;
}
async function pinnedCallAdmission(name: string, argumentsValue: Record<string, unknown>): Promise<{ error?: string; route?: PinnedRoute }> {
  const staticError = deniedCapability(argumentsValue);
  if (staticError) return { error: staticError };
  if (name !== "read" || typeof argumentsValue.path !== "string") return { route: { route: "file" } };
  try {
    const route = await classifyPinnedRead(argumentsValue.path);
    if (route.route === "file") return { route };
    const capability = routeCapability(route.route);
    if (capability === undefined) {
      return { error: "OMP capability denial policy unavailable: unknown pinned read route", route };
    }
    const entry = sessionState.capabilityDenials[capability];
    if (!entry || entry.capability !== capability || typeof entry.message !== "string") {
      return { error: `OMP capability denial policy unavailable: ${capability}`, route };
    }
    return { error: entry.message, route };
  } catch (error) {
    return { error: `OMP pinned read classification failed closed: ${String(error)}` };
  }
}
function parametersFor(tool: any): Record<string, unknown> {
  if (sessionState.irToJsonSchema === null || typeof tool.parameters !== "function" || tool.parameters.ir === undefined) {
    throw new Error(`tool schema is unavailable: ${tool.name}`);
  }
  const source = sessionState.irToJsonSchema(tool.parameters.ir, { io: "input", dialect: null });
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
  sessionState.runtimeInputs = requireRuntimeInputs(payload.runtime_inputs);
  if (
    typeof payload.workspace !== "string"
    || payload.workspace !== sessionState.runtimeInputs.cwd
    || typeof payload.package_dir !== "string"
    || payload.package_dir !== sessionState.runtimeInputs.package_dir
    || typeof payload.scratch !== "string"
    || !payload.scratch
  ) {
    throw new Error("initialize workspace authority does not match runtime_inputs");
  }
  const advertisement = requireAdvertisement(payload.advertisement);
  const leaseModel = requireLeaseModel(payload.model_config);
  sessionState.capabilityDenials = advertisement.capabilityDenials;
  sessionState.workspace = sessionState.runtimeInputs.cwd;
  process.env.HOME = sessionState.runtimeInputs.home;
  const routeClassifier = requireRouteClassifier(payload.route_classifier);
  sessionState.pinnedSourceRoot = routeClassifier.sourceRoot;
  const expectedFiles = new Map(routeClassifier.moduleDigests);
  expectedFiles.set("bun.lock", routeClassifier.lockSha256);
  for (const [relativePath, expectedDigest] of expectedFiles) {
    const absolutePath = `${sessionState.pinnedSourceRoot}/${relativePath}`;
    let observedDigest: string;
    try {
      observedDigest = await sha256File(absolutePath);
    } catch (error) {
      throw new Error(`pinned OMP source verification failed for ${relativePath}: ${String(error)}`);
    }
    if (observedDigest !== expectedDigest) {
      throw new Error(`pinned OMP source verification failed for ${relativePath}: expected ${expectedDigest}, got ${observedDigest}`);
    }
  }
  for (const [relativePath, digest] of Object.entries({
    "packages/coding-agent/src/sdk.ts": "87fea78a3b4f72e6dae959cee0a94afa0395aa6a954ab2d1154962200bb0759e",
    "packages/coding-agent/src/system-prompt.ts": "3e86ad7cbbd76546b6571dcc04d2c5e46a94df67986b2f6036c6cebe60d31d88",
    "packages/coding-agent/src/prompts/system/system-prompt.md": "6f4854f0e80a3a0931c9b34bda8a8d7e6f90d15ec4dfa7567c5f7019b02feca7",
    "packages/coding-agent/src/prompts/system/personalities/default.md": "2c8bd4f78b9223f33c4d38044256f1285acbfb932eaad42d1cc5a3e2fc64dbd3",
    "packages/coding-agent/src/session/date-cwd-reminder.ts": "57d5a86126d78e332af182fa0d70165e0a3719a70b55296121cfd4d1c291a709",
    "packages/coding-agent/src/utils/local-date.ts": "a4950542abd04a81b58a2607d459bbffe4a450bb76832056d1b91de0e8ae728e",
    "packages/coding-agent/src/prompts/system/date-cwd-reminder.md": "624bf012a8960a5546eb5b555e63221a87612a3dcd608f84976c66ab48a92769",
    "packages/ai/src/providers/openai-completions.ts": "8492ebc5f6fc0e024a310f13aa00487f7f41d34dd28f3e91a29633b88610cd60",
    "packages/omptype/src/json-schema.ts": "827b4718ab1c92bce0156e44749e25843024c445b2fd32ce6ed9f574e1408cf7",
    "packages/ai/src/utils/validation.ts": "55de91a79bdb24d9c9a8069d8f373661652c392dcc88f8078ed8a3992d200563",
    "packages/catalog/src/hosts.ts": "5d404265744877b04aa82b7edd1596d9c20f9faacf7018227939c594d3b9c262",
    "packages/coding-agent/src/config/model-registry.ts": "7d2158bba572adc98b7288f21819020a233622e34d633d12c93bbce76f0ce915",
    "packages/ai/src/auth-storage.ts": "e663b1cb5997db912ca0b93689a43186aee1adf50b1bb9bf20003be85f82323d",
  })) {
    if (await sha256File(`${sessionState.pinnedSourceRoot}/${relativePath}`) !== digest) {
      throw new Error(`pinned OMP request source verification failed for ${relativePath}`);
    }
  }
  // A known-host provider id would make pinned compat resolution assert that
  // host for the BreadBoard route. hosts.ts has no imports, so this refusal
  // precedes loading the SDK graph.
  const { KNOWN_HOSTS, modelMatchesHost } = await import(`${sessionState.pinnedSourceRoot}/packages/catalog/src/hosts.ts`);
  for (const host of Object.keys(KNOWN_HOSTS)) {
    if (advertisement.providerId === host || modelMatchesHost({ provider: advertisement.providerId, baseUrl: "" }, host)) {
      throw new Error(`advertisement.model_registry.provider_id names pinned known host ${host}`);
    }
  }
  // The pinned source root is deployment-selected; static source imports cannot
  // represent the verified runtime path used by the SIF installation.
  const { createAgentSession, Settings } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/sdk.ts`);
  const { ModelRegistry } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/config/model-registry.ts`);
  const { AuthStorage } = await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/auth-storage.ts`);
  const providerModule = await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/providers/openai-completions.ts`);
  sessionState.convertMessages = providerModule.convertMessages as typeof sessionState.convertMessages;
  sessionState.parseChunkUsage = providerModule.parseChunkUsage;
  sessionState.streamOpenAICompletions = providerModule.streamOpenAICompletions;
  ({ createInitialResponsesAssistantMessage: sessionState.createInitialAssistantMessage } =
    await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/providers/openai-shared.ts`));
  const schemaModule = await import(`${sessionState.pinnedSourceRoot}/packages/omptype/src/json-schema.ts`);
  sessionState.irToJsonSchema = schemaModule.irToJsonSchema as typeof sessionState.irToJsonSchema;
  const validatorModule = await import(`${sessionState.pinnedSourceRoot}/packages/ai/src/utils/validation.ts`);
  sessionState.validateToolArguments = validatorModule.validateToolArguments as typeof sessionState.validateToolArguments;
  const { SessionManager } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/session-manager.ts`);
  const { DateCwdReminderInjector } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/date-cwd-reminder.ts`);
  sessionState.reminderInjector = new DateCwdReminderInjector();
  const { formatLocalCalendarDate } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/utils/local-date.ts`);
  sessionState.currentDate = formatLocalCalendarDate(new Date(Number(sessionState.runtimeInputs.message_timestamp_ms)));
  const compactionEnabled = payload.compaction === true;
  sessionState.workerCompactionEnabled = compactionEnabled;
  sessionState.compactionPkg = await import(`${sessionState.pinnedSourceRoot}/packages/agent/src/compaction/compaction.ts`);
  sessionState.compactionUtils = await import(`${sessionState.pinnedSourceRoot}/packages/agent/src/compaction/utils.ts`);
  sessionState.compactionMethods = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/compaction-methods.ts`);
  sessionState.shakePkg = await import(`${sessionState.pinnedSourceRoot}/packages/agent/src/compaction/shake.ts`);
  sessionState.tokenizerPkg = await import(`${sessionState.pinnedSourceRoot}/packages/agent/src/tokenizer.ts`);
  sessionState.summarizationSystemPromptText = sessionState.compactionUtils.SUMMARIZATION_SYSTEM_PROMPT;
  sessionState.autoContinuePromptText = await Bun.file(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/prompts/system/auto-continue.md`).text();
  sessionState.queuedMessagesPkg = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/queued-messages.ts`);
  ({ convertToLlm: sessionState.convertToLlm } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/messages.ts`));
  ({ TodoTracker: sessionState.TodoTracker } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/session/todo-tracker.ts`));
  const contextUsageModule = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/modes/utils/context-usage.ts`);
  sessionState.computeNonMessageTokensFn = contextUsageModule.computeNonMessageTokens as typeof sessionState.computeNonMessageTokensFn;
  const overrides: Record<string, any> = {
    "retry.enabled": false, "retry.fallbackChains": {},
    "autoContinue.enabled": false, "prewalk.enabled": false, "imageUrls.enabled": false,
    "title.refreshOnReplan": false,
    "tools.maxTimeout": 30, "tools.artifactSpillThreshold": 32768, "edit.fuzzyMatch": true,
    "edit.fuzzyThreshold": 0.95, "edit.enforceSeenLines": true, "edit.autoRepair.enabled": false,
    "edit.blockAutoGenerated": true, "shellMinimizer.enabled": false,
  };
  if (!compactionEnabled) {
    overrides["compaction.enabled"] = false;
    overrides["snapcompact.enabled"] = false;
  }
  const settings = await Settings.init({ cwd: sessionState.workspace, agentDir: String(payload.scratch), inMemory: true, configFiles: [], overrides });
  const manager = await withJournalMetadata(() => SessionManager.inMemory(sessionState.workspace));
  const authStorage = await AuthStorage.create(":memory:");
  const modelRegistry = new ModelRegistry(authStorage, `${payload.scratch}/models.yml`, { settings });
  modelRegistry.registerProvider(advertisement.providerId, {
    baseUrl: leaseModel.baseUrl,
    api: "openai-completions",
    apiKey: WORKER_API_KEY,
    models: [leaseModel.definition],
  });
  const model = modelRegistry.find(advertisement.providerId, leaseModel.definition.id);
  if (!model) throw new Error("pinned OMP model registry did not bind the lease model");
  // Pinned sdk.ts:1642 (and :2312/:2636/:2688) calls preconnectModelHost,
  // which at sdk.ts:4351-4360 opens an idle socket to the lease base URL via
  // globalThis.fetch.preconnect. The conductor owns every provider connection,
  // so the worker must open none. fetch.preconnect is non-configurable, so
  // replace fetch for the worker's lifetime with a wrapper that has no
  // preconnect; sdk.ts:4354 then returns before opening a socket.
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => nativeFetch(input, init)) as typeof fetch;
  const created = await withJournalMetadata(() => createAgentSession({
    cwd: sessionState.workspace, agentDir: String(payload.scratch), authStorage, modelRegistry, model, thinkingLevel: "off",
    toolNames: [...TOOL_NAMES], restrictToolNames: true, allowRestrictedCustomTools: false,
    settings, sessionManager: manager, contextFiles: [], skills: [], rules: [], promptTemplates: [],
    slashCommands: [], customTools: [], extensions: [], additionalExtensionPaths: [],
    disableExtensionDiscovery: true, enableMCP: false, enableLsp: false, enableIrc: false,
    skipPythonPreflight: true, hasUI: false, interactivePrompts: false,
    rebindModelAfterDiscovery: false, getApiKey: async () => WORKER_API_KEY,
  }));
  sessionState.session = created.session;
  sessionState.nativeSystemPrompt = sessionState.session.agent.state.systemPrompt;
  if (!Array.isArray(sessionState.nativeSystemPrompt) || !sessionState.nativeSystemPrompt.length || sessionState.nativeSystemPrompt.some((part) => typeof part !== "string")) {
    throw new Error("pinned OMP SDK did not build a system prompt");
  }
  sessionState.tools = sessionState.session.agent.state.tools.filter((tool: any) => TOOL_NAMES.includes(tool.name));
  sessionState.boundedDescriptions = Object.fromEntries(sessionState.tools.map((tool: any) => {
    const policy = advertisement.descriptionPolicy[tool.name];
    let description = String(tool.description);
    if (new Bun.CryptoHasher("sha256").update(description).digest("hex") !== digestHex(policy.original_sha256, `${tool.name}.original_sha256`)) {
      throw new Error(`pinned OMP description differs for ${tool.name}`);
    }
    for (const span of policy.removed_spans as Array<{ text: string }>) {
      if (description.split(span.text).length !== 2) throw new Error(`bounded OMP description span differs for ${tool.name}`);
      description = description.replace(span.text, "");
    }
    if (new Bun.CryptoHasher("sha256").update(description).digest("hex") !== digestHex(policy.bounded_sha256, `${tool.name}.bounded_sha256`)) {
      throw new Error(`bounded OMP description differs for ${tool.name}`);
    }
    return [tool.name, description];
  }));
  const originalRead = sessionState.tools.find((tool: any) => tool.name === "read");
  if (!originalRead || await sha256File(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/tools/read.ts`) !== PINNED_READ_SHA256) {
    throw new Error("pinned read dispatcher verification failed");
  }
  observePinnedReadBranches(sessionState.pinnedSourceRoot);
  const { ReadTool: RouteReadTool } = await import(`${sessionState.pinnedSourceRoot}/packages/coding-agent/src/tools/read.ts?omp-route-observer`);
  sessionState.pinnedReadTool = new RouteReadTool(originalRead.session);
  return {
    schema_version: PHASE_SCHEMA,
    kind: "initialized",
    system_prompt: sessionState.nativeSystemPrompt.join("\n\n"),
    tool_schemas: sessionState.tools.map((tool: any) => ({ type: "function", function: { name: tool.name, description: sessionState.boundedDescriptions[tool.name], parameters: parametersFor(tool) } })),
    bootstrap: {
      ...sessionState.runtimeInputs,
      current_date: sessionState.currentDate,
      consumer_id: "breadboard.oh-my-pi.v18.1.17",
      workspace: sessionState.workspace,
      source_commit: sessionState.pinnedSourceRoot.split("-").at(-1),
      settings: payload.advertisement.settings ?? {},
      capability_denials: advertisement.capabilityDenials,
    },
  };
}




// Conductor owns transport, so the pinned registry never sends this key.
const WORKER_API_KEY = "omp-tool-worker-key";
















async function prepareTools(payload: Record<string, any>): Promise<Record<string, any>> {
    if (sessionState.validateToolArguments === null) throw new Error("pinned OMP tool validator is unavailable");
    sessionState.prepared = await Promise.all((payload.calls ?? []).map(async (call: { id: string; name: string; arguments: unknown }) => {
      let argumentsValue = call.arguments;
      if (typeof argumentsValue === "string") {
        try {
          argumentsValue = JSON.parse(argumentsValue);
        } catch {
          // Pinned stream parsing can finalize a partial object without its
          // required field; leave schema validation in charge of the error.
          argumentsValue = {};
        }
      }
      const item: Call & { error?: string; route?: PinnedRoute } = {
        id: String(call.id),
        name: String(call.name),
        arguments: argumentsValue !== null && typeof argumentsValue === "object" && !Array.isArray(argumentsValue)
          ? argumentsValue as Record<string, unknown> : {},
      };
      const tool = sessionState.tools.find((candidate: { name: string }) => candidate.name === item.name);
      if (!tool) {
        item.error = `OMP tool is not admitted: ${item.name}`;
        return item;
      }
      const admission = await pinnedCallAdmission(item.name, item.arguments);
      item.route = admission.route;
      if (admission.error) {
        item.error = admission.error;
        return item;
      }
      try {
        item.arguments = sessionState.validateToolArguments(tool, {
          type: "toolCall", id: item.id, name: item.name, arguments: argumentsValue,
        });
      } catch (error) {
        item.error = error instanceof Error ? error.message : String(error);
      }
      return item;
    }));
    return { schema_version: PHASE_SCHEMA, kind: "prepared", calls: sessionState.prepared, history_calls: sessionState.prepared.map((call) => ({ id: call.id, name: call.name, arguments: call.arguments })) };
  }

async function executeBatch(payload: Record<string, any>): Promise<Record<string, any>> {
    const completed: Array<Record<string, unknown> & { source_index: number }> = [];
    await Promise.all(sessionState.prepared.map(async (call, sourceIndex) => {
      if (call.error) {
        completed.push({ id: call.id, completion_index: completed.length, content: call.error, details: { phase: "prepare", effects: {} }, isError: true, source_index: sourceIndex });
        return;
      }
      const tool = sessionState.tools.find((candidate: any) => candidate.name === call.name);
      if (!tool) {
        completed.push({ id: call.id, completion_index: completed.length, content: `OMP tool is not admitted: ${call.name}`, details: { effects: {} }, isError: true, source_index: sourceIndex });
        return;
      }
      try {
        const result = await tool.execute(call.id, call.arguments);
        const rawDetails = result.details && typeof result.details === "object" && !Array.isArray(result.details) ? result.details : {};
        const details = { ...rawDetails, effects: rawDetails.effects && typeof rawDetails.effects === "object" && !Array.isArray(rawDetails.effects) ? rawDetails.effects : {} };
        completed.push({ id: call.id, completion_index: completed.length, content: result.content ?? [], details, isError: Boolean(result.details?.isError), terminate: Boolean(result.details?.terminate), source_index: sourceIndex });
      } catch (error) {
        completed.push({ id: call.id, completion_index: completed.length, content: String(error), details: { phase: "execute", effects: {} }, isError: true, source_index: sourceIndex });
      }
    }));
    completed.sort((left, right) => left.source_index - right.source_index);
    return { schema_version: PHASE_SCHEMA, kind: "tool_results", results: completed.map(({ source_index, ...result }) => result) };
  }

async function close(payload: Record<string, any>): Promise<Record<string, any>> {
    const owned = await descendantHandles(process.pid);
    let disposeError: unknown;
    try {
      await sessionState.session.dispose();
    } catch (error) {
      disposeError = error;
    }
    sessionState.session = null;
    sessionState.tools = [];
    const processes = await reapDescendants(owned);
    if (disposeError) throw disposeError;
    return { schema_version: PHASE_SCHEMA, kind: "closed", cleanup: { processes, all_dead: processes.length === 0 } };
  }



export { PHASE_SCHEMA, WORKER_API_KEY, close, executeBatch, initialize, parametersFor, prepareTools, sessionState };
