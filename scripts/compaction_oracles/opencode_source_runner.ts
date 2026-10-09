/** Execute pinned compaction code with scripted session/LLM ports.
 * Install AI SDK 5.0.124 at OPENCODE_AI_SDK_ROOT or in the source checkout.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

type Part = { type: string; text?: string; mime?: string; filename?: string; url?: string;
  image_url?: { url: string }; id?: string; messageID?: string; callID?: string; tool?: string;
  state?: { status?: string; output?: string; input?: Record<string, unknown>; time?: { compacted?: number } } };
type ChatMessage = { role: string; content?: string | Part[]; summary?: boolean; tool_call_id?: string; name?: string };
type Info = { id: string; role: string; summary?: boolean; agent?: string; model?: { providerID: string; modelID: string } };
type NativeMessage = { info: Info; parts: Part[] };
type SourceModel = { id: string; providerID: string; api: { npm: string; id: string } };
type ModelMessage = { role: string; content: string | Array<{ type: string; text?: string; output?: { type: string; value: string } }>; summary?: boolean };
type SourceRequest = { messages: ModelMessage[]; tools: Record<string, unknown> };
type SourceProcessor = { message: Info; process: (request: SourceRequest) => Promise<string> };
type SourceProcess = (input: { parentID: string; sessionID: string; messages: NativeMessage[]; auto: boolean; overflow: boolean; abort: AbortSignal }) => Promise<string>;
export type RecaptureCase = { preset: string; input: { messages: ChatMessage[]; reason: string; summary_responses: string[]; context_window: number; usage: { total_tokens: number } }; expect: unknown; source: { evidence: string[] }; capture: { notes: string } };

export async function sourceRunner(opencode: string, plugin: string) {
  const transpiler = new Bun.Transpiler({ loader: "ts" });
  const slice = (path: string, first: number, last: number) =>
    readFileSync(path, "utf8").split("\n").slice(first - 1, last).join("\n");
  function compile<T>(source: string, names: string[], values: unknown[], returned: string): T {
    // Trusted, pinned source is evaluated at a typed collaborator boundary.
    return new Function(...names, transpiler.transformSync(source.replaceAll("export ", "")) + `\nreturn ${returned};`)(...values) as T;
  }
  const sdkRoot = process.env.OPENCODE_AI_SDK_ROOT ?? opencode;
  // The dependency path is selected by the capture environment, not the repository.
  const { convertToModelMessages } = await import(Bun.resolveSync("ai", sdkRoot));
  const messagePath = join(opencode, "packages/opencode/src/session/message-v2.ts");
  const compactionPath = join(opencode, "packages/opencode/src/session/compaction.ts");
  const isMedia = compile<(mime: string) => boolean>(slice(messagePath, 20, 22), [], [], "isMedia");
  let nextID = 0;
  const Identifier = { ascending: () => `generated-${nextID++}` };
  const toModel = compile<(messages: NativeMessage[], model: SourceModel, options?: { stripMedia: boolean }) => ModelMessage[]>(slice(messagePath, 496, 729),
    ["convertToModelMessages", "isMedia", "iife", "Identifier", "MessageV2"],
    [convertToModelMessages, isMedia, (fn: () => unknown) => fn(), Identifier, {}], "toModelMessages");
  const directivePath = join(plugin, "src/shared/system-directive.ts");
  const createDirective = compile<(type: string) => string>(slice(directivePath, 8, 17), [], [], "createSystemDirective");
  const directiveTypes = compile<Record<string, string>>(slice(directivePath, 49, 58), [], [], "SystemDirectiveTypes");
  const contextSource = readFileSync(join(plugin, "src/hooks/compaction-context-injector/hook.ts"), "utf8");
  const injector = compile<() => () => string>(contextSource.slice(contextSource.indexOf("const COMPACTION_CONTEXT_PROMPT")),
    ["createSystemDirective", "SystemDirectiveTypes"], [createDirective, directiveTypes], "createCompactionContextInjector")();
  const model = { id: "oracle-model", providerID: "oracle", api: { npm: "@ai-sdk/openai", id: "oracle-model" } };
  async function summarize(caseData: RecaptureCase) {
    nextID = 0;
    const inp = caseData.input;
    const messages: NativeMessage[] = inp.messages.map((message, i) => ({
      info: { id: `input-${i}`, role: message.role, summary: message.summary, agent: "oracle", model: { providerID: "oracle", modelID: "oracle-model" } },
      parts: (typeof message.content === "string" ? [{ type: "text", text: message.content }] : message.content ?? []).map(part => {
        if (part.type !== "image_url") return structuredClone(part);
        const url = part.image_url!.url;
        return { type: "file", mime: /^data:([^;,]+)/.exec(url)?.[1] ?? "image/png", url, filename: part.filename };
      }),
    }));
    const marker: NativeMessage = { info: { id: "marker", role: "user", agent: "oracle", model: { providerID: "oracle", modelID: "oracle-model" } }, parts: [{ type: "compaction" }] };
    messages.push(marker);
    const stored: NativeMessage[] = [marker];
    const requests: Array<{ system: string; messages: ModelMessage[]; tools: unknown[] }> = [];
    let selected: number[] = [];
    const responses = [...inp.summary_responses];
    const MessageV2 = { toModelMessages: (items: NativeMessage[], selectedModel: SourceModel, options: { stripMedia: boolean }) => {
      selected = items.filter(m => m.info.id !== "marker").map(m => Number(m.info.id.slice(6)));
      return toModel(items, selectedModel, options);
    }, ContextOverflowError: class { constructor(public data: unknown) {} toObject() { return this.data; } } };
    const Session = {
      updateMessage: async (info: Info) => { stored.push({ info, parts: [] }); return info; },
      updatePart: async (part: Part) => { stored.find(m => m.info.id === part.messageID)!.parts.push(part); },
    };
    const SessionProcessor = { create: ({ assistantMessage }: { assistantMessage: Info }): SourceProcessor => ({
      message: assistantMessage,
      process: async request => {
        if (responses.length === 0) throw new Error("Unrecorded summary response");
        requests.push({ system: readFileSync(join(opencode, "packages/opencode/src/agent/prompt/compaction.txt"), "utf8"), messages: request.messages, tools: Object.values(request.tools) });
        stored.find(m => m.info.id === assistantMessage.id)!.parts.push({ type: "text", text: responses.shift() });
        return "continue";
      },
    }) };
    const processCompaction = compile<SourceProcess>(slice(compactionPath, 101, 294),
      ["Agent", "Provider", "Session", "Identifier", "Instance", "SessionProcessor", "Plugin", "MessageV2", "Bus", "Event"],
      [{ get: async () => ({}) }, { getModel: async () => model }, Session, Identifier, { directory: "oracle", worktree: "oracle" }, SessionProcessor,
       { trigger: async () => ({ context: caseData.preset.startsWith("oh-my-opencode") ? [injector()] : [], prompt: undefined }) }, MessageV2, { publish: () => {} }, { Compacted: "session.compacted" }], "process");
    await processCompaction({ parentID: "marker", sessionID: "oracle", messages, auto: inp.reason !== "manual", overflow: inp.reason === "overflow", abort: new AbortController().signal });
    if (responses.length !== 0) throw new Error("Recorded summary responses not consumed");
    const replay = inp.messages.map((m, i) => m.role === "user" ? i : -1).filter(i => i >= 0 && !selected.includes(i));
    const selection: { summarize: number[]; replay?: number[] } = { summarize: selected };
    if (replay.length) selection.replay = replay;
    caseData.expect = { summary_requests: requests, selection, projected_view: stored.map(message => {
      const result = toModel([message], model)[0];
      if (message.info.summary) result.summary = true;
      return result;
    }) };
    caseData.source.evidence = ["packages/opencode/src/session/compaction.ts:101-294", "packages/opencode/src/session/message-v2.ts:490-729"];
    if (caseData.preset.startsWith("oh-my-opencode")) caseData.source.evidence.push("src/hooks/compaction-context-injector/hook.ts:7-71", "src/shared/system-directive.ts:8-17,49-58");
    caseData.capture.notes = "Executed pinned SessionCompaction.process and MessageV2.toModelMessages with AI SDK 5.0.124, scripted LLM and in-memory session persistence. Plugin context comes from the pinned injector. Selection excludes the synthetic marker, whose rendered text remains in the request and projection.";
  }
  async function recover(caseData: RecaptureCase) {
    const recoveryRoot = join(plugin, "src/hooks/anthropic-context-window-limit-recovery");
    const storageSource = readFileSync(join(recoveryRoot, "storage-paths.ts"), "utf8");
    const truncationMessage = /TRUNCATION_MESSAGE\s*=\s*"([^"]+)"/.exec(storageSource)![1];
    const native: NativeMessage[] = caseData.input.messages.filter(m => m.role === "tool").map(m => ({ info: { id: m.tool_call_id!, role: "assistant" }, parts: [{ id: m.tool_call_id!, type: "tool", callID: m.tool_call_id, tool: m.name ?? "bash", state: { status: "completed", input: {}, output: String(m.content), time: {} } }] }));
    const edits: Array<{ index: number; message: ChatMessage }> = [];
    const patchPart = async (_client: unknown, _session: string, messageID: string, _partID: string, updated: Part) => {
      const message = native.find(m => m.info.id === messageID)!;
      message.parts = [updated];
      const rendered = toModel([message], model).find(m => m.role === "tool")!;
      if (typeof rendered.content === "string") throw new Error("Expected AI SDK tool-result parts");
      const index = caseData.input.messages.findIndex(m => m.tool_call_id === messageID);
      edits.push({ index, message: { ...caseData.input.messages[index], content: rendered.content[0].output!.value } });
      return true;
    };
    const truncate = compile<(client: unknown, session: string, message: string, partID: string, part: Part) => Promise<unknown>>(slice(join(recoveryRoot, "tool-result-storage-sdk.ts"), 61, 93),
      ["TRUNCATION_MESSAGE", "patchPart", "log"], [truncationMessage, patchPart, () => {}], "truncateToolResultAsync");
    const truncateUntil = compile<(session: string, current: number, max: number, ratio: number, chars: number, client: unknown) => Promise<{ sufficient: boolean }>>(slice(join(recoveryRoot, "target-token-truncation.ts"), 26, 196),
      ["isSqliteBackend", "normalizeSDKResponse", "truncateToolResultAsync", "findToolResultsBySize", "truncateToolResult"],
      [() => true, (response: { data: NativeMessage[] }) => response.data, truncate, () => { throw new Error("Unexpected legacy storage branch"); }, () => { throw new Error("Unexpected legacy storage branch"); }], "truncateUntilTargetTokens");
    const result = await truncateUntil("oracle", caseData.input.usage.total_tokens, caseData.input.context_window, 0.5, 4, { session: { messages: async () => ({ data: native }) } });
    if (!result.sufficient) throw new Error("Recovery fixture failed to recover");
    caseData.expect = { selection: { targets: edits.map(e => e.index) }, edits };
    caseData.capture.notes = "Executed pinned truncateUntilTargetTokens SQLite branch and truncateToolResultAsync with an in-memory PATCH port; expected edits are native model-visible outputs from pinned MessageV2.toModelMessages and AI SDK 5.0.124.";
  }
  return { summarize, recover, injector };
}
