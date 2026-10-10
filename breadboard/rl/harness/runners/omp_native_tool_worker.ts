#!/opt/omp/runtime/bun-linux-x64-baseline/bun
import { readdir, readFile, realpath } from "node:fs/promises";

// Persistent tool-only phase worker. The Conductor owns provider transport and
// the model loop; this process only composes and executes the four SDK tools.
const RPC_SCHEMA = "bb.native-worker.rpc.v1";
const PHASE_SCHEMA = "bb.omp-native.v1";
const TOOL_NAMES = ["read", "bash", "edit", "write"] as const;
type Call = { id: string; name: string; arguments: Record<string, unknown> };
type RouteClassifier = { sourceRoot: string; lockSha256: string; moduleDigests: Map<string, string> };
let pinnedSourceRoot = "";
let boundedDescriptions: Record<string, string> = {};
let nativeSystemPrompt: string[] = [];
let capabilityDenials: Record<string, Record<string, unknown>> = {};
let session: any = null;
let tools: any[] = [];
let irToJsonSchema: ((ir: unknown, options?: Record<string, unknown>) => Record<string, unknown>) | null = null;
let validateToolArguments: ((tool: unknown, call: { type: "toolCall"; id: string; name: string; arguments: unknown }) => Record<string, unknown>) | null = null;
let workspace = "";
let runtimeInputs: Record<string, string> = {};
let convertMessages: ((model: any, context: any, compat: any) => unknown[]) | null = null;
let reminderInjector: { transform: (context: { systemPrompt: string[]; messages: unknown[] }, date: string, cwd: string) => unknown } | null = null;
// Conductor owns transport, so the pinned registry never sends this key.
const WORKER_API_KEY = "omp-tool-worker-key";
let workerCompactionEnabled = false;
let compactionPkg: any = null;
let compactionUtils: any = null;
let compactionMethods: any = null;
let shakePkg: any = null;
let tokenizerPkg: any = null;
let queuedMessagesPkg: { isTerminalTextAssistantAnswer: (message: unknown) => boolean };
let TodoTracker: new (host: unknown) => { buildPostCompactionEagerNudges: () => Array<Record<string, unknown>> };
let parseChunkUsage: (usage: object, model: unknown, premiumRequests: undefined, timestamp: number) => unknown;
let createInitialAssistantMessage: (api: string, provider: string, model: string) => { usage: unknown };
let summarizationSystemPromptText = "";
let autoContinuePromptText = "";
let convertToLlm: (messages: unknown[]) => unknown[];
let computeNonMessageTokensFn: (session: unknown, tokenizer: unknown) => number;
let streamOpenAICompletions: (
  model: unknown,
  context: { systemPrompt: string[]; messages: unknown[]; tools: unknown[] },
  options: { apiKey: string; fetch: typeof fetch; maxRetries: number },
) => AsyncIterable<{ type: string; error?: Record<string, unknown> }>;
const COMPACTION_RECOVERY_BAND = 0.8; // session-maintenance.ts:215
 
 async function sha256File(path: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await Bun.file(path).arrayBuffer());
  return Buffer.from(digest).toString("hex");
}

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
  const input = exactRecord(
    value,
    "runtime_inputs",
    ["cwd", "home", "current_date", "package_dir"],
  );
  const typed: Record<string, string> = {};
  for (const name of ["cwd", "home", "current_date", "package_dir"]) {
    if (typeof input[name] !== "string" || !input[name]) {
      throw new Error(`runtime_inputs.${name} must be a non-empty string`);
    }
    typed[name] = input[name] as string;
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
let pinnedReadTool: ReadRouteTool | null = null;

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
  if (typeof value !== "string" || pinnedReadTool === null) return { route: "unclassified" };
  try {
    await pinnedReadTool.execute("read-route-observation", { path: value });
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
    const entry = capabilityDenials[capability];
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
    const entry = capabilityDenials[capability];
    if (!entry || entry.capability !== capability || typeof entry.message !== "string") {
      return { error: `OMP capability denial policy unavailable: ${capability}`, route };
    }
    return { error: entry.message, route };
  } catch (error) {
    return { error: `OMP pinned read classification failed closed: ${String(error)}` };
  }
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
  runtimeInputs = requireRuntimeInputs(payload.runtime_inputs);
  if (
    typeof payload.workspace !== "string"
    || payload.workspace !== runtimeInputs.cwd
    || typeof payload.package_dir !== "string"
    || payload.package_dir !== runtimeInputs.package_dir
    || typeof payload.scratch !== "string"
    || !payload.scratch
  ) {
    throw new Error("initialize workspace authority does not match runtime_inputs");
  }
  const advertisement = requireAdvertisement(payload.advertisement);
  const leaseModel = requireLeaseModel(payload.model_config);
  capabilityDenials = advertisement.capabilityDenials;
  workspace = runtimeInputs.cwd;
  process.env.HOME = runtimeInputs.home;
  const routeClassifier = requireRouteClassifier(payload.route_classifier);
  pinnedSourceRoot = routeClassifier.sourceRoot;
  const expectedFiles = new Map(routeClassifier.moduleDigests);
  expectedFiles.set("bun.lock", routeClassifier.lockSha256);
  for (const [relativePath, expectedDigest] of expectedFiles) {
    const absolutePath = `${pinnedSourceRoot}/${relativePath}`;
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
    "packages/coding-agent/src/prompts/system/date-cwd-reminder.md": "624bf012a8960a5546eb5b555e63221a87612a3dcd608f84976c66ab48a92769",
    "packages/ai/src/providers/openai-completions.ts": "8492ebc5f6fc0e024a310f13aa00487f7f41d34dd28f3e91a29633b88610cd60",
    "packages/omptype/src/json-schema.ts": "827b4718ab1c92bce0156e44749e25843024c445b2fd32ce6ed9f574e1408cf7",
    "packages/ai/src/utils/validation.ts": "55de91a79bdb24d9c9a8069d8f373661652c392dcc88f8078ed8a3992d200563",
    "packages/catalog/src/hosts.ts": "5d404265744877b04aa82b7edd1596d9c20f9faacf7018227939c594d3b9c262",
    "packages/coding-agent/src/config/model-registry.ts": "7d2158bba572adc98b7288f21819020a233622e34d633d12c93bbce76f0ce915",
    "packages/ai/src/auth-storage.ts": "e663b1cb5997db912ca0b93689a43186aee1adf50b1bb9bf20003be85f82323d",
  })) {
    if (await sha256File(`${pinnedSourceRoot}/${relativePath}`) !== digest) {
      throw new Error(`pinned OMP request source verification failed for ${relativePath}`);
    }
  }
  // A known-host provider id would make pinned compat resolution assert that
  // host for the BreadBoard route. hosts.ts has no imports, so this refusal
  // precedes loading the SDK graph.
  const { KNOWN_HOSTS, modelMatchesHost } = await import(`${pinnedSourceRoot}/packages/catalog/src/hosts.ts`);
  for (const host of Object.keys(KNOWN_HOSTS)) {
    if (advertisement.providerId === host || modelMatchesHost({ provider: advertisement.providerId, baseUrl: "" }, host)) {
      throw new Error(`advertisement.model_registry.provider_id names pinned known host ${host}`);
    }
  }
  // The pinned source root is deployment-selected; static source imports cannot
  // represent the verified runtime path used by the SIF installation.
  const { createAgentSession, Settings } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/sdk.ts`);
  const { ModelRegistry } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/config/model-registry.ts`);
  const { AuthStorage } = await import(`${pinnedSourceRoot}/packages/ai/src/auth-storage.ts`);
  const providerModule = await import(`${pinnedSourceRoot}/packages/ai/src/providers/openai-completions.ts`);
  convertMessages = providerModule.convertMessages as typeof convertMessages;
  parseChunkUsage = providerModule.parseChunkUsage;
  streamOpenAICompletions = providerModule.streamOpenAICompletions;
  ({ createInitialResponsesAssistantMessage: createInitialAssistantMessage } =
    await import(`${pinnedSourceRoot}/packages/ai/src/providers/openai-shared.ts`));
  const schemaModule = await import(`${pinnedSourceRoot}/packages/omptype/src/json-schema.ts`);
  irToJsonSchema = schemaModule.irToJsonSchema as typeof irToJsonSchema;
  const validatorModule = await import(`${pinnedSourceRoot}/packages/ai/src/utils/validation.ts`);
  validateToolArguments = validatorModule.validateToolArguments as typeof validateToolArguments;
  const { SessionManager } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/session-manager.ts`);
  const { DateCwdReminderInjector } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/date-cwd-reminder.ts`);
  reminderInjector = new DateCwdReminderInjector();
  const compactionEnabled = payload.compaction === true;
  workerCompactionEnabled = compactionEnabled;
  compactionPkg = await import(`${pinnedSourceRoot}/packages/agent/src/compaction/compaction.ts`);
  compactionUtils = await import(`${pinnedSourceRoot}/packages/agent/src/compaction/utils.ts`);
  compactionMethods = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/compaction-methods.ts`);
  shakePkg = await import(`${pinnedSourceRoot}/packages/agent/src/compaction/shake.ts`);
  tokenizerPkg = await import(`${pinnedSourceRoot}/packages/agent/src/tokenizer.ts`);
  summarizationSystemPromptText = compactionUtils.SUMMARIZATION_SYSTEM_PROMPT;
  autoContinuePromptText = await Bun.file(`${pinnedSourceRoot}/packages/coding-agent/src/prompts/system/auto-continue.md`).text();
  queuedMessagesPkg = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/queued-messages.ts`);
  ({ convertToLlm } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/messages.ts`));
  ({ TodoTracker } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/session/todo-tracker.ts`));
  const contextUsageModule = await import(`${pinnedSourceRoot}/packages/coding-agent/src/modes/utils/context-usage.ts`);
  computeNonMessageTokensFn = contextUsageModule.computeNonMessageTokens as typeof computeNonMessageTokensFn;
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
  const settings = await Settings.init({ cwd: workspace, agentDir: String(payload.scratch), inMemory: true, configFiles: [], overrides });
  const manager = await withJournalMetadata(() => SessionManager.inMemory(workspace));
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
    cwd: workspace, agentDir: String(payload.scratch), authStorage, modelRegistry, model, thinkingLevel: "off",
    toolNames: [...TOOL_NAMES], restrictToolNames: true, allowRestrictedCustomTools: false,
    settings, sessionManager: manager, contextFiles: [], skills: [], rules: [], promptTemplates: [],
    slashCommands: [], customTools: [], extensions: [], additionalExtensionPaths: [],
    disableExtensionDiscovery: true, enableMCP: false, enableLsp: false, enableIrc: false,
    skipPythonPreflight: true, hasUI: false, interactivePrompts: false,
    rebindModelAfterDiscovery: false, getApiKey: async () => WORKER_API_KEY,
  }));
  session = created.session;
  nativeSystemPrompt = session.agent.state.systemPrompt;
  if (!Array.isArray(nativeSystemPrompt) || !nativeSystemPrompt.length || nativeSystemPrompt.some((part) => typeof part !== "string")) {
    throw new Error("pinned OMP SDK did not build a system prompt");
  }
  tools = session.agent.state.tools.filter((tool: any) => TOOL_NAMES.includes(tool.name));
  boundedDescriptions = Object.fromEntries(tools.map((tool: any) => {
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
  const originalRead = tools.find((tool: any) => tool.name === "read");
  if (!originalRead || await sha256File(`${pinnedSourceRoot}/packages/coding-agent/src/tools/read.ts`) !== PINNED_READ_SHA256) {
    throw new Error("pinned read dispatcher verification failed");
  }
  observePinnedReadBranches(pinnedSourceRoot);
  const { ReadTool: RouteReadTool } = await import(`${pinnedSourceRoot}/packages/coding-agent/src/tools/read.ts?omp-route-observer`);
  pinnedReadTool = new RouteReadTool(originalRead.session);
  return {
    schema_version: PHASE_SCHEMA,
    kind: "initialized",
    system_prompt: nativeSystemPrompt.join("\n\n"),
    tool_schemas: tools.map((tool: any) => ({ type: "function", function: { name: tool.name, description: boundedDescriptions[tool.name], parameters: parametersFor(tool) } })),
    bootstrap: {
      ...runtimeInputs,
      consumer_id: "breadboard.oh-my-pi.v18.1.17",
      workspace,
      source_commit: pinnedSourceRoot.split("-").at(-1),
      settings: payload.advertisement.settings ?? {},
      capability_denials: advertisement.capabilityDenials,
    },
  };
}
// The stock SessionManager owns the full episode journal, including entries
// absent from its reduced context. Append glue: agent-session.ts:2734;
// compaction commit: session-maintenance.ts:1859-1878.
let journalMessageId = 0;
let journalView: unknown[] = [];

async function withJournalMetadata<T>(operation: () => T | Promise<T>, performanceClock?: () => number): Promise<T> {
  const NativeDate = Date;
  const randomUUID = crypto.randomUUID;
  const randomUUIDv7 = Bun.randomUUIDv7;
  const performanceNow = performance.now;
  const timestamp = 1000 + journalMessageId;
  class ReplayDate extends NativeDate {
    constructor(value: string | number = timestamp) { super(value); }
    static now() { return timestamp; }
  }
  globalThis.Date = ReplayDate as DateConstructor;
  crypto.randomUUID = () => `00000000-0000-4000-8000-${(++journalMessageId).toString(16).padStart(12, "0")}`;
  Bun.randomUUIDv7 = () => "00000000-0000-7000-8000-000000000000";
  if (performanceClock) performance.now = performanceClock;
  try { return await operation(); }
  finally {
    globalThis.Date = NativeDate;
    crypto.randomUUID = randomUUID;
    Bun.randomUUIDv7 = randomUUIDv7;
    if (performanceClock) performance.now = performanceNow;
  }
}

async function synchronizeHistory(messages: unknown[]): Promise<Array<Record<string, unknown>>> {
  if (messages.length < journalView.length ||
      journalView.some((message, index) => JSON.stringify(message) !== JSON.stringify(messages[index]))) {
    throw new Error("OMP history diverged from the committed stock session context");
  }
  await withJournalMetadata(() => {
    for (const message of messages.slice(journalView.length)) {
      const msg = message as Record<string, unknown>;
      if (msg.role === "compactionSummary") {
        throw new Error("compactionSummary must originate in this episode's stock journal");
      }
      session.sessionManager.appendMessage(structuredClone(msg));
    }
  });
  journalView = structuredClone(messages);
  session.agent.replaceMessages(structuredClone(messages));
  return session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
    entry.type === "message" || entry.type === "compaction");
}

async function shakeEntries(config: unknown): Promise<{ tokensFreed: number; toolResultsDropped: number; blocksDropped: number }> {
  // Run stock SessionMaintenance.shake via the real SDK session. The manager is
  // in-memory (session-manager.ts:3115-3121); its stock saveArtifact returns a
  // deterministic counter (2128-2136), including the stock recovery link.
  // Deterministic metadata only: session-manager.ts:99, :2302;
  // session-migrations.ts:7; shake.ts:448. Restore clocks/ids before tool work.
  return await withJournalMetadata(async () => {
    session.agent.replaceMessages(session.sessionManager.buildSessionContext().messages);
    return await session.shake("elide", { config });
  });
}
class ReplaySentinelError extends Error {
  readonly isReplaySentinel = true;
  constructor() {
    super("REPLAY_SENTINEL");
  }
}

class ReplayDriver {
  answers: string[];
  capturedCalls: Array<{
    messages: unknown[];
    max_tokens?: number;
    tool_choice?: string;
  }> = [];
  callIndex = 0;

  constructor(answers: string[] = []) {
    this.answers = answers;
  }

  completeImpl = async (
    modelParam: unknown,
    ctx: { systemPrompt?: unknown; messages: Array<Record<string, unknown>>; tools?: unknown },
    opts: { maxTokens?: number; toolChoice?: string; reasoning?: unknown },
  ) => {
    const idx = this.callIndex++;
    const targetModel = (modelParam && typeof modelParam === "object") ? modelParam : session.agent.state.model;
    const wireMessages = convertMessages!(targetModel, ctx, (targetModel as Record<string, unknown>).compat);
    const callRecord: { messages: unknown[]; max_tokens?: number; tool_choice?: string } = {
      messages: wireMessages,
      max_tokens: typeof opts?.maxTokens === "number" ? opts.maxTokens : undefined,
    };
    if (typeof opts?.toolChoice === "string") {
      callRecord.tool_choice = opts.toolChoice;
    }
    this.capturedCalls.push(callRecord);
    if (idx < this.answers.length) {
      return {
        role: "assistant",
        content: [{ type: "text", text: this.answers[idx] }],
        stopReason: "stop",
        timestamp: 2000,
      };
    }
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 10);
    await promise;
    throw new ReplaySentinelError();
  };
}

async function buildHandoffContext(entries: Array<Record<string, unknown>>) {
  const handoffPromptText = compactionPkg.renderHandoffPrompt(compactionPkg.AUTO_HANDOFF_THRESHOLD_FOCUS);
  const agentMessages = session.sessionManager.buildSessionContext().messages;
  const handoffSnapshot = [
    ...agentMessages,
    {
      role: "user",
      content: [{ type: "text", text: handoffPromptText }],
      attribution: "agent",
      timestamp: 1000 + agentMessages.length,
    },
  ];
  // session-handoff.ts:150-157: run the stock session conversion and the
  // agent's side-request builder, with the base rather than per-turn prompt.
  // Pin the clock read by the stock date/cwd transform.
  const NativeDate = Date;
  const timestamp = NativeDate.parse(`${runtimeInputs.current_date}T12:00:00`);
  class ReplayDate extends NativeDate {
    constructor(value: string | number = timestamp) { super(value); }
    static now() { return timestamp; }
  }
  globalThis.Date = ReplayDate as DateConstructor;
  try {
    const handoffLlmMessages = await session.convertMessagesToLlm(handoffSnapshot);
    return await session.agent.buildSideRequestContext(handoffLlmMessages, nativeSystemPrompt);
  } finally {
    globalThis.Date = NativeDate;
  }
}
function compactionCreatedRetryFit(
  compactedMessages: Array<Record<string, unknown>>,
  contextWindow: number,
  compactionSettings: Record<string, unknown>,
  headroom = false,
): boolean {
  const tokenizer = new tokenizerPkg.Tokenizer(session.model);
  const nonMessageTokens = computeNonMessageTokensFn!(session, tokenizer);
  // session-stats.ts:289-291: after compaction (unanchored), getContextUsage returns nonMessageTokens + tokenizer.countMessages(messages)
  const providerUsageTokens = nonMessageTokens + tokenizer.countMessages(compactedMessages);
  const storedContextTokens = nonMessageTokens + tokenizer.countMessages(compactedMessages, { excludeEncryptedReasoning: true });
  // session-maintenance.ts:3088-3091
  const residualTokens = compactionPkg.compactionContextTokens(providerUsageTokens, storedContextTokens);
  // session-maintenance.ts:3039-3053 and :3088-3093.
  const fitBudget = headroom
    ? Math.floor(compactionPkg.resolveThresholdTokens(contextWindow, compactionSettings) * COMPACTION_RECOVERY_BAND)
    : Math.max(0, contextWindow - compactionPkg.resolveBudgetReserveTokens(contextWindow, compactionSettings));
  return residualTokens <= fitBudget;
}

// session-maintenance.ts:3131-3162 (#rescueCompactionDeadEnd)
async function rescueCompactionDeadEnd(
  contextWindow: number,
  compactionSettings: Record<string, unknown>,
  options: { skipElide: boolean; headroom?: boolean },
): Promise<{ success: boolean; messages?: Array<Record<string, unknown>> }> {
  if (options.skipElide) return { success: false };
  const result = await shakeEntries(shakePkg.RESCUE_SHAKE_CONFIG);
  if (result.toolResultsDropped + result.blocksDropped === 0) return { success: false };
  const postRescueMessages = session.sessionManager.buildSessionContext().messages;
  if (compactionCreatedRetryFit(postRescueMessages, contextWindow, compactionSettings, options.headroom)) {
    return { success: true, messages: postRescueMessages };
  }
  return { success: false };
}

async function commitStockCompaction(prep: Record<string, unknown>, payload: Record<string, unknown>): Promise<void> {
  for (const [key, value] of Object.entries(prep.settings as Record<string, unknown>)) {
    session.settings.set(`compaction.${key}`, value);
  }
  const answers = [payload.summary, payload.turn_prefix_summary,
    ...(Array.isArray(payload.followup_summaries) ? payload.followup_summaries : [])]
    .filter((answer): answer is string => typeof answer === "string");
  const nativeFetch = globalThis.fetch;
  let answerIndex = 0;
  await withJournalMetadata(async () => {
    const replayDate = Date;
    // Provider transport is the only replay seam. The source's real
    // SessionMaintenance commits details, method, kept-entry IDs and tokensAfter
    // (session-maintenance.ts:1859-1878; public entry points :775, :1561).
    globalThis.fetch = (async () => {
      if (answerIndex === answers.length) throw new Error("stock compaction replay answers exhausted");
      const chunk = { id: "compaction-replay", object: "chat.completion.chunk",
        created: 2, model: session.model.id, choices: [{
          index: 0, delta: { role: "assistant", content: answers[answerIndex++] }, finish_reason: "stop",
        }] };
      globalThis.Date = replayDate;
      return new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`,
        { headers: { "content-type": "text/event-stream" } });
    }) as typeof fetch;
    try {
      if (prep.method === "handoff") {
        class HandoffDate extends replayDate {
          constructor(value: string | number = replayDate.parse(`${runtimeInputs.current_date}T12:00:00`)) { super(value); }
        }
        globalThis.Date = HandoffDate as DateConstructor;
        await session.handoff(compactionPkg.AUTO_HANDOFF_THRESHOLD_FOCUS);
      } else {
        await session.compact();
      }
    } finally { globalThis.fetch = nativeFetch; globalThis.Date = replayDate; }
  });
}

async function prepareCompactionPhase(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!workerCompactionEnabled) {
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "compaction_disabled",
    };
  }
  const rawMessages = Array.isArray(payload.messages) ? payload.messages : [];
  const entries = await synchronizeHistory(rawMessages);
  const settings = {
    ...session.settings.getGroup("compaction"),
    ...(typeof payload.settings === "object" && payload.settings !== null ? payload.settings : {}),
  };
  const model = session.agent.state.model;
  let tokenizer = new tokenizerPkg.Tokenizer(model);
  if (typeof payload.context_window !== "number" || payload.context_window <= 0 || !Number.isFinite(payload.context_window)) {
    throw new Error("prepare_compaction requires positive context_window");
  }
  const contextWindow = payload.context_window;
  // Stock invalidates an assistant's pre-rewrite bill after a compaction
  // (session-maintenance.ts:2465-2477), including auto-continue checkpoints.
  const lastAssistantIndex = entries.findLastIndex(entry =>
    entry.type === "message" && (entry.message as Record<string, unknown>).role === "assistant");
  const lastCompactionIndex = entries.findLastIndex(entry => entry.type === "compaction");
  const usage = lastAssistantIndex <= lastCompactionIndex ? undefined
    : payload.usage && typeof payload.usage === "object"
      ? parseChunkUsage(payload.usage, model, undefined, 1000 + entries.length)
      : (entries[lastAssistantIndex].message as Record<string, unknown>).usage;
  // Verbatim stored-context floor, session-maintenance.ts:1924-1936, :2115-2118.
  const storedTokens = computeNonMessageTokensFn(session, tokenizer) +
    tokenizer.countMessages(session.agent.state.messages, { excludeEncryptedReasoning: true });
  const contextTokens = usage
    ? compactionPkg.compactionContextTokens(compactionPkg.calculateContextTokens(usage), storedTokens)
    : storedTokens;

  const reason = typeof payload.reason === "string" ? payload.reason : "threshold";
  // The error remains in the source journal, but checkCompaction removes it
  // from the active context before recovery (session-maintenance.ts:2277).
  if (reason === "overflow") {
    const lastMessage = rawMessages.at(-1);
    if (lastMessage && typeof lastMessage === "object" &&
        "role" in lastMessage && lastMessage.role === "assistant" &&
        "stopReason" in lastMessage && lastMessage.stopReason === "error") {
      session.agent.replaceMessages(rawMessages.slice(0, -1));
    }
  }
  if (reason === "threshold") {
    if (!compactionPkg.shouldCompact(contextTokens, contextWindow, settings)) {
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_unavailable",
        reason: "not_triggered",
      };
    }
  }

  const configuredOrder = settings.methodOrder ?? compactionMethods.DEFAULT_COMPACTION_METHOD_ORDER;
  const methods = compactionMethods.resolveCompactionMethodOrder(configuredOrder);
  let selectedMethod: string | undefined;
  let fallbackFromShake = false;
  let historyRewritten = false;
  let currentEntries = entries;

  for (const candidate of methods) {
    if (candidate === "remote") {
      if (compactionMethods.canUseRemoteCompaction(model, compactionMethods.resolveMethodSettings(settings, candidate))) {
        selectedMethod = candidate;
        break;
      }
      continue;
    }
    if (candidate === "snapcompact") {
      if (model?.input?.includes("image") === true) {
        selectedMethod = candidate;
        break;
      }
      continue;
    }
    if (candidate === "handoff") {
      if (reason === "overflow") continue;
      selectedMethod = candidate;
      break;
    }
    if (candidate === "shake") {
      const shakeConfig = {
        ...shakePkg.DEFAULT_SHAKE_CONFIG,
        ...(typeof settings.shake === "object" && settings.shake !== null ? settings.shake : {}),
      };
      const result = await shakeEntries(shakeConfig);
      const tokensFreed = result.tokensFreed;
      historyRewritten ||= result.toolResultsDropped + result.blocksDropped > 0;
      currentEntries = session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
        entry.type === "message" || entry.type === "compaction");
      // SDK and source imports have distinct message-cache registries; a
      // rewritten journal needs fresh local estimates.
      tokenizer = new tokenizerPkg.Tokenizer(model);
      const reclaimed = result.toolResultsDropped + result.blocksDropped > 0;
      let stillOverThreshold = false;
      if (contextWindow > 0) {
        const postShakeTokens = Math.max(0, contextTokens - tokensFreed);
        const thresholdTokens = compactionPkg.resolveThresholdTokens(contextWindow, settings);
        const recoveryBand = Math.floor(thresholdTokens * COMPACTION_RECOVERY_BAND);
        stillOverThreshold = postShakeTokens > recoveryBand;
      }
      const shouldFallBack = reason !== "idle" && ((reason === "overflow" && !reclaimed) || stillOverThreshold);
      if (!shouldFallBack) {
        const shakenMessages = session.sessionManager.buildSessionContext().messages;
        return {
          schema_version: PHASE_SCHEMA,
          kind: "compaction_prepared",
           preparation: {
             method: "shake",
             tokensBefore: contextTokens,
             shakenMessages,
            contextWindow,
            checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
            rawMessages,
            settings,
             reason,
           },
          summary_request: null,
          turn_prefix_request: null,
        };
      }
      fallbackFromShake = true;
      continue;
    }
    if (candidate === "soft") {
      selectedMethod = candidate;
      break;
    }
  }

  if (selectedMethod !== "handoff" && selectedMethod !== "soft") {
    selectedMethod = "soft";
  }

  const effectiveSettings = compactionMethods.resolveMethodSettings(settings, selectedMethod);
  let preparation = compactionPkg.prepareCompaction(currentEntries, effectiveSettings, model, tokenizer);
  // Automatic maintenance rescues a recent turn before abandoning preparation
  // (session-maintenance.ts:3637-3695). Use the source's elide operation, including
  // its artifact counter, then prepare from the rewritten journal.
  if (!preparation && reason !== "idle" && !fallbackFromShake) {
    const result = await shakeEntries(shakePkg.RESCUE_SHAKE_CONFIG);
    if (result.toolResultsDropped + result.blocksDropped > 0) {
      historyRewritten = true;
      currentEntries = session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
        entry.type === "message" || entry.type === "compaction");
      tokenizer = new tokenizerPkg.Tokenizer(model);
      preparation = compactionPkg.prepareCompaction(currentEntries, effectiveSettings, model, tokenizer);
    }
  }
  if (!preparation) {
    const unavailable = {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_unavailable",
      reason: "nothing_to_compact",
    };
    if (!historyRewritten) return unavailable;
    // Stock keeps an elided branch even when no summary cut becomes possible
    // (session-maintenance.ts:3762-3771); the conductor must commit this view.
    const messages = session.agent.state.messages;
    journalView = structuredClone(messages);
    return { ...unavailable, history_rewritten: true, messages };
  }
  const driver = new ReplayDriver([]);
  if (selectedMethod === "handoff") {
    const handoffContext = await buildHandoffContext(currentEntries);
    try {
      await compactionPkg.generateHandoffFromContext(handoffContext, model, {
        completeImpl: driver.completeImpl,
        streamOptions: { apiKey: WORKER_API_KEY },
      });
    } catch (e: unknown) {
      if (!(e instanceof ReplaySentinelError)) throw e;
    }
    const firstKeptIndex = currentEntries.findIndex((e) => e.id === preparation.firstKeptEntryId);
    return {
      schema_version: PHASE_SCHEMA,
      kind: "compaction_prepared",
      preparation: {
         method: "handoff",
         firstKeptIndex,
         firstKeptEntryId: preparation.firstKeptEntryId,
         tokensBefore: preparation.tokensBefore,
        timestamp: 1000 + currentEntries.length,
        contextWindow,
        checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
         rawMessages,
        settings,
        reason,
        fallbackFromShake,
        fileOps: {
          read: Array.from(preparation.fileOps.read),
          written: Array.from(preparation.fileOps.written),
          edited: Array.from(preparation.fileOps.edited),
        },
      },
      summary_request: driver.capturedCalls[0] ?? null,
      turn_prefix_request: null,
    };
  }

  // selectedMethod === "soft"

  try {
    await compactionPkg.compact(preparation, model, WORKER_API_KEY, undefined, undefined, {
      completeImpl: driver.completeImpl,
      remoteSystemPrompt: [summarizationSystemPromptText],
    });
  } catch (e: unknown) {
    if (!(e instanceof ReplaySentinelError)) throw e;
  }

  const firstKeptIndex = currentEntries.findIndex((e) => e.id === preparation.firstKeptEntryId);
  let summaryRequest: { messages: Array<{ role: string; content: string }>; max_tokens?: number } | null = null;
  let turnPrefixRequest: { messages: Array<{ role: string; content: string }>; max_tokens?: number } | null = null;
  if (preparation.isSplitTurn && preparation.turnPrefixMessages && preparation.turnPrefixMessages.length > 0) {
    if (preparation.messagesToSummarize.length > 0 || preparation.previousSummary) {
      summaryRequest = driver.capturedCalls[0] ?? null;
      turnPrefixRequest = driver.capturedCalls[1] ?? null;
    } else {
      turnPrefixRequest = driver.capturedCalls[0] ?? null;
    }
  } else {
    summaryRequest = driver.capturedCalls[0] ?? null;
  }

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_prepared",
    preparation: {
       method: "soft",
       firstKeptIndex,
       firstKeptEntryId: preparation.firstKeptEntryId,
       tokensBefore: preparation.tokensBefore,
      timestamp: 1000 + currentEntries.length,
      contextWindow,
      checkpoint: typeof payload.checkpoint === "string" ? payload.checkpoint : undefined,
       settings,
      rawMessages,
      reason,
      fallbackFromShake,
    },
    summary_request: summaryRequest,
    turn_prefix_request: turnPrefixRequest,
  };
}

async function finalizeCompactionPhase(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const prep = (payload.preparation && typeof payload.preparation === "object") ? (payload.preparation as Record<string, unknown>) : null;
  if (!prep) {
    throw new Error("finalize_compaction requires preparation");
  }
  const summary = typeof payload.summary === "string" ? payload.summary : "";
  const model = session.agent.state.model;
  const tokenizer = new tokenizerPkg.Tokenizer(model);
  const settings = {
    ...session.settings.getGroup("compaction"),
    ...(typeof prep.settings === "object" && prep.settings !== null ? prep.settings : {}),
  };
  const contextWindow = prep.contextWindow;
  if (typeof contextWindow !== "number" || !Number.isFinite(contextWindow) || contextWindow <= 0) {
    throw new Error("finalize_compaction requires positive context_window");
  }
  const isOverflow = prep.reason === "overflow";
 
   let compactionResult: {
    messages?: Array<Record<string, unknown>>;
    compactionMessage?: Record<string, unknown>;
    firstKeptIndex?: number;
    summary?: string;
  };

  if (prep.method === "shake") {
    const shaken = Array.isArray(prep.shakenMessages) ? (prep.shakenMessages as Array<Record<string, unknown>>) : [];
    compactionResult = {
      messages: shaken,
    };
  } else if (prep.method === "handoff") {
    compactionResult = {};
  } else if (prep.method === "soft") {
    const entries = session.sessionManager.getBranch().filter((entry: Record<string, unknown>) =>
      entry.type === "message" || entry.type === "compaction");
    const preparation = compactionPkg.prepareCompaction(entries, settings, model, tokenizer);
    if (!preparation) {
      throw new Error("finalize_compaction could not reconstruct preparation");
    }

    const answers: string[] = [];
    if (preparation.isSplitTurn && preparation.turnPrefixMessages && preparation.turnPrefixMessages.length > 0) {
      if (preparation.messagesToSummarize.length > 0 || preparation.previousSummary) {
        if (typeof payload.summary === "string") answers.push(payload.summary);
        if (typeof payload.turn_prefix_summary === "string") answers.push(payload.turn_prefix_summary);
      } else {
        if (typeof payload.turn_prefix_summary === "string") answers.push(payload.turn_prefix_summary);
      }
    } else {
      if (typeof payload.summary === "string") answers.push(payload.summary);
    }
    const followups = Array.isArray(payload.followup_summaries) ? (payload.followup_summaries as unknown[]) : [];
    for (const f of followups) {
      if (typeof f === "string") answers.push(f);
    }

    const driver = new ReplayDriver(answers);
    let compactResult: Record<string, unknown> | null = null;
    try {
      compactResult = (await compactionPkg.compact(preparation, model, WORKER_API_KEY, undefined, undefined, {
        completeImpl: driver.completeImpl,
        remoteSystemPrompt: [summarizationSystemPromptText],
      })) as Record<string, unknown>;
    } catch (e: unknown) {
      if (!(e instanceof ReplaySentinelError)) throw e;
    }

    if (!compactResult) {
      const nextCall = driver.capturedCalls[answers.length];
      return {
        schema_version: PHASE_SCHEMA,
        kind: "compaction_followup_request",
        request: nextCall,
      };
    }

    compactionResult = {};
  } else {
    throw new Error(`unknown compaction method: ${String(prep.method)}`);
  }
  let retry = true;
  let finalMessages = compactionResult.messages;
  if (!finalMessages) {
    await commitStockCompaction(prep, payload);
    const entry = session.sessionManager.getBranch().findLast(
      (item: Record<string, unknown>) => item.type === "compaction");
    if (!entry) throw new Error("stock maintenance did not commit a compaction");
    compactionResult.compactionMessage = {
      role: "compactionSummary", summary: entry.summary, shortSummary: entry.shortSummary,
      tokensBefore: entry.tokensBefore, firstKeptEntryId: entry.firstKeptEntryId,
      details: entry.details, method: entry.method, timestamp: Date.parse(entry.timestamp),
    };
    compactionResult.firstKeptIndex = session.sessionManager.getBranch()
      .filter((item: Record<string, unknown>) => item.type === "message" || item.type === "compaction")
      .findIndex((item: Record<string, unknown>) => item.id === entry.firstKeptEntryId);
    compactionResult.summary = entry.summary;
    finalMessages = session.sessionManager.buildSessionContext().messages;
  }
  if (isOverflow) {
    let compactedMessages = finalMessages;

    // Drop trailing assistant turn with stopReason "error" before retry check (session-maintenance.ts:4380-4392)
    const lastMsg = compactedMessages[compactedMessages.length - 1];
    if (lastMsg?.role === "assistant" && lastMsg.stopReason === "error") {
      compactedMessages = compactedMessages.slice(0, -1);
    }
    finalMessages = compactedMessages;

    let retryFits = compactionCreatedRetryFit(compactedMessages, contextWindow, settings);
    if (!retryFits) {
      // Rescue pass (session-maintenance.ts:4402 & 3131-3162)
      const skipElide = Boolean(prep.fallbackFromShake || prep.method === "shake");
      const rescueResult = await rescueCompactionDeadEnd(
        contextWindow,
        settings,
        { skipElide },
      );
      if (rescueResult.success && rescueResult.messages) {
        retryFits = true;
        finalMessages = rescueResult.messages;
      }
    }
    retry = retryFits;
  }
  let hasHeadroom = true;
  if (!isOverflow && prep.reason !== "idle") {
    hasHeadroom = compactionCreatedRetryFit(finalMessages, contextWindow, settings, true);
    if (!hasHeadroom) {
      const rescued = await rescueCompactionDeadEnd(
        contextWindow, settings,
        { skipElide: Boolean(prep.fallbackFromShake), headroom: true },
      );
      hasHeadroom = rescued.success;
      if (rescued.messages) finalMessages = rescued.messages;
    }
  }

  const checkpoint = prep.checkpoint ?? payload.checkpoint;
  let continuation: Array<Record<string, unknown>> | undefined;
  const lastAssistant = (prep.rawMessages as Array<Record<string, unknown>> | undefined)?.findLast(m => m.role === "assistant");
  // agent-session.ts:3854-3898: a terminal text answer without an active goal
  // does not continue. RL has no goal/task/todo tools; run the stock eager builder.
  if (checkpoint === "agent_end" && hasHeadroom && settings.autoContinue !== false && !queuedMessagesPkg.isTerminalTextAssistantAnswer(lastAssistant)) {
    const tracker = new TodoTracker({
      settings: session.settings,
      agentKind: () => session.agentKind,
      planModeEnabled: () => session.planModeEnabled,
      getEnabledToolNames: () => tools.map(tool => tool.name),
    });
    const eagerNudges = tracker.buildPostCompactionEagerNudges();
    continuation = [
      ...eagerNudges,
      {
        role: "developer",
        content: [{ type: "text", text: autoContinuePromptText }],
        attribution: "agent",
        timestamp: 1000 + (Array.isArray(prep.rawMessages) ? prep.rawMessages.length : 0),
        synthetic: true,
      },
    ];
  }
  session.agent.replaceMessages(finalMessages);
  journalView = structuredClone(finalMessages);

  return {
    schema_version: PHASE_SCHEMA,
    kind: "compaction_finalized",
    messages: finalMessages,
    compaction_message: compactionResult.compactionMessage,
    first_kept_index: compactionResult.firstKeptIndex,
    summary: compactionResult.summary,
    ...(isOverflow ? { retry } : {}),
    ...(continuation ? { continuation } : {}),
  };
}

function typeOfInvalidHttpFailure(payload: Record<string, unknown>): boolean {
  return typeof payload.http_status !== "number" || !Number.isInteger(payload.http_status) ||
    typeof payload.response_body_text !== "string" || !Array.isArray(payload.messages);
}

async function dispatch(operation: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (operation === "initialize") return initialize(payload as Record<string, any>);
  if (operation === "prepare_compaction") return prepareCompactionPhase(payload);
  if (operation === "finalize_compaction") return finalizeCompactionPhase(payload);
  if (operation === "project_provider_failure") {
    if (!session || typeOfInvalidHttpFailure(payload)) {
      throw new Error("project_provider_failure requires a session, HTTP status, body and messages");
    }
    const status = payload.http_status as number;
    const body = payload.response_body_text as string;
    const duration = payload.provider_request_duration_ms;
    if (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0) {
      throw new Error("project_provider_failure requires recorded provider request duration");
    }
    // Replay stock's own subtraction (openai-completions.ts:677,1496), not
    // a post-hoc message edit. The elapsed clock advances at transport completion.
    let elapsed = 0;
    return await withJournalMetadata(async () => {
      let message: Record<string, unknown> | undefined;
      for await (const event of streamOpenAICompletions(
        session.agent.state.model,
        { systemPrompt: nativeSystemPrompt, messages: payload.messages as unknown[], tools: [] },
        { apiKey: WORKER_API_KEY, fetch: async () => {
          elapsed = duration;
          return new Response(body, { status });
        }, maxRetries: 0 },
      )) {
        if (event.type === "error") message = event.error;
      }
      if (!message) throw new Error("pinned provider did not emit an error assistant");
      return { schema_version: PHASE_SCHEMA, kind: "provider_failure", message };
    }, () => elapsed);
  }
  if (operation === "parse_usage") {
    // The stock provider starts with its exported initial usage when no chunk
    // reports usage (openai-completions.ts:681; openai-shared.ts:3530-3547).
    return await withJournalMetadata(() => ({
      schema_version: PHASE_SCHEMA, kind: "assistant_usage",
      usage: payload.usage == null
        ? createInitialAssistantMessage(session.model.api, session.model.provider, session.model.id).usage
        : parseChunkUsage(payload.usage as object, session.model, undefined, 1000 + journalMessageId),
    }));
  }
  if (operation === "project_request") {
    if (convertMessages === null) throw new Error("pinned OMP provider converter is unavailable");
    const model = session.agent.state.model;
    if (!model) throw new Error("pinned OMP session model is unavailable");
    if (model.api !== "openai-completions") throw new Error(`pinned OMP provider converter requires an OpenAI Completions model: ${String(model.api)}`);
    if (reminderInjector === null) throw new Error("pinned OMP date/cwd reminder is unavailable");
    const messages = convertMessages(model, reminderInjector.transform({ systemPrompt: nativeSystemPrompt, messages: convertToLlm(payload.messages as unknown[]) }, runtimeInputs.current_date, workspace), model.compat);
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
    if (validateToolArguments === null) throw new Error("pinned OMP tool validator is unavailable");
    prepared = await Promise.all((payload.calls ?? []).map(async (call: { id: string; name: string; arguments: unknown }) => {
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
      const tool = tools.find((candidate: { name: string }) => candidate.name === item.name);
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
        item.arguments = validateToolArguments(tool, {
          type: "toolCall", id: item.id, name: item.name, arguments: argumentsValue,
        });
      } catch (error) {
        item.error = error instanceof Error ? error.message : String(error);
      }
      return item;
    }));
    return { schema_version: PHASE_SCHEMA, kind: "prepared", calls: prepared, history_calls: prepared.map((call) => ({ id: call.id, name: call.name, arguments: call.arguments })) };
  }
  if (operation === "execute_batch") {
    const completed: Array<Record<string, unknown> & { source_index: number }> = [];
    await Promise.all(prepared.map(async (call, sourceIndex) => {
      if (call.error) {
        completed.push({ id: call.id, completion_index: completed.length, content: call.error, details: { phase: "prepare", effects: {} }, isError: true, source_index: sourceIndex });
        return;
      }
      const tool = tools.find((candidate: any) => candidate.name === call.name);
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
async function writeFrame(value: Record<string, unknown>) {
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
    if (payload.schema_version !== RPC_SCHEMA || typeof payload.request_id !== "number") throw new Error("invalid worker frame");
    try {
      const result = await dispatch(payload.operation, payload.payload);
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, result });
    } catch (error) {
      await writeFrame({ schema_version: RPC_SCHEMA, request_id: payload.request_id, error: { type: "WorkerPhaseError", message: String(error) } });
    }
  }
}
