import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';

const repoRoot = resolve(process.env.PI_REPO_ROOT ?? '.');
const fixtureRoot = resolve(process.env.P5_FIXTURE_ROOT ?? '/tmp/pi_p5_residual_fixture_workspace');
const agentDir = resolve(process.env.P5_AGENT_DIR ?? '/tmp/pi_p5_residual_agent_dir');
const cwd = join(fixtureRoot, 'project');
const sessionDir = join(agentDir, 'sessions', 'p5-residual');

rmSync(fixtureRoot, { recursive: true, force: true });
rmSync(agentDir, { recursive: true, force: true });
mkdirSync(cwd, { recursive: true });
mkdirSync(sessionDir, { recursive: true });
mkdirSync(join(cwd, '.pi', 'extensions'), { recursive: true });
mkdirSync(agentDir, { recursive: true });

const extensionPath = join(cwd, '.pi', 'extensions', 'residual-hook.mjs');
writeFileSync(extensionPath, `
export default function(api) {
  const log = globalThis.__PI_P5_RESIDUAL_EVENTS ?? (globalThis.__PI_P5_RESIDUAL_EVENTS = []);
  const record = (event, payload = {}) => log.push({ event, payload });
  api.registerFlag('residual-flag', { type: 'boolean', default: true, description: 'Residual evidence flag' });
  api.registerCommand('residual-command', { description: 'Residual command', handler: async (_args, ctx) => { record('command_handler', { cwd: ctx.cwd, hasUI: ctx.hasUI }); } });
  api.registerTool({
    name: 'residual_tool',
    label: 'Residual Tool',
    description: 'Provider-free residual hook tool',
    promptSnippet: 'residual_tool: provider-free residual probe',
    parameters: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false },
    execute: async (_toolCallId, params) => ({ content: [{ type: 'text', text: 'residual:' + params.value }], details: { observed: params.value }, isError: false })
  });
  api.on('resources_discover', (event) => { record('resources_discover', { cwd: event.cwd, reason: event.reason }); return { skillPaths: ['skills/residual'], promptPaths: ['prompts/residual'], themePaths: ['themes/residual'] }; });
  api.on('context', (event) => { record('context', { messages: event.messages.length }); return { messages: [...event.messages, { role: 'custom', customType: 'residual_context', content: [{ type: 'text', text: 'residual context' }], display: false }] }; });
  api.on('before_provider_request', (event) => { record('before_provider_request', { hasPayload: event.payload !== undefined }); return { ...event.payload, residualHook: true }; });
  api.on('before_agent_start', (event) => { record('before_agent_start', { prompt: event.prompt, systemPrompt: event.systemPrompt }); return { message: { customType: 'residual_agent_start', content: 'residual start', display: false }, systemPrompt: event.systemPrompt + '\\nresidual-start' }; });
  api.on('tool_call', (event) => { record('tool_call', { toolName: event.toolName }); return { block: event.toolName === 'write', reason: event.toolName === 'write' ? 'residual write blocked' : undefined }; });
  api.on('tool_result', (event) => { record('tool_result', { toolName: event.toolName, isError: event.isError }); return { isError: false, content: [{ type: 'text', text: 'residual tool result' }], details: { residual: true } }; });
  api.on('user_bash', (event) => { record('user_bash', { command: event.command, excludeFromContext: event.excludeFromContext }); return {}; });
  api.on('session_before_switch', (event) => { record('session_before_switch', { reason: event.reason, targetSessionFile: event.targetSessionFile ?? null }); return { cancel: false }; });
  api.on('session_before_fork', (event) => { record('session_before_fork', { entryId: event.entryId }); return { cancel: false, skipConversationRestore: true }; });
  api.on('session_before_compact', (event) => { record('session_before_compact', { branchEntries: event.branchEntries.length, customInstructions: event.customInstructions ?? null }); return { compaction: { summary: 'extension compact summary', firstKeptEntryId: event.branchEntries.at(-1)?.id ?? 'none', tokensBefore: 4242, details: { source: 'residual-hook' } } }; });
  api.on('session_compact', (event) => { record('session_compact', { compactionEntryType: event.compactionEntry.type, fromExtension: event.fromExtension }); });
  api.on('session_before_tree', (event) => { record('session_before_tree', { targetId: event.preparation.targetId, userWantsSummary: event.preparation.userWantsSummary }); return { summary: { summary: 'tree summary from extension', details: { source: 'residual-hook' } }, label: 'residual-label' }; });
  api.on('session_tree', (event) => { record('session_tree', { newLeafId: event.newLeafId, oldLeafId: event.oldLeafId, fromExtension: event.fromExtension ?? false }); });
}
`, 'utf8');

process.env.PI_CODING_AGENT_DIR = agentDir;
process.env.PI_OFFLINE = '1';
process.chdir(repoRoot);

const loaderModule = await import(join(repoRoot, 'packages/coding-agent/src/core/extensions/loader.ts'));
const runnerModule = await import(join(repoRoot, 'packages/coding-agent/src/core/extensions/runner.ts'));
const sessionModule = await import(join(repoRoot, 'packages/coding-agent/src/core/session-manager.ts'));
const configModule = await import(join(repoRoot, 'packages/coding-agent/src/config.ts'));

const { loadExtensions } = loaderModule;
const { ExtensionRunner } = runnerModule;
const { SessionManager, buildSessionContext, loadEntriesFromFile, parseSessionEntries } = sessionModule;

const loaded = await loadExtensions([extensionPath], cwd);
const session = SessionManager.create(cwd, sessionDir);
const userEntryId = session.appendMessage({ role: 'user', content: [{ type: 'text', text: 'hello residual' }], timestamp: 10 });
const assistantEntryId = session.appendMessage({ role: 'assistant', content: [{ type: 'text', text: 'answer residual' }], provider: 'no-provider', model: 'residual-model', usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2 }, timestamp: 20 });
const customEntryId = session.appendCustomMessageEntry('residual_custom', [{ type: 'text', text: 'custom context' }], false, { retained: true });
const compactionEntryId = session.appendCompaction('manual compact summary', userEntryId, 3210, { readFiles: ['a.ts'], modifiedFiles: ['b.ts'] }, false);
session.branch(assistantEntryId);
const branchSummaryEntryId = session.branchWithSummary(assistantEntryId, 'branch summary residual', { reason: 'fork' }, true);
const branchedSessionPath = session.createBranchedSession(branchSummaryEntryId);
const forked = SessionManager.forkFrom(branchedSessionPath, join(fixtureRoot, 'forked-project'), join(agentDir, 'sessions', 'forked'));
const continued = SessionManager.continueRecent(cwd, sessionDir);
const reopened = SessionManager.open(branchedSessionPath, sessionDir);

const runner = new ExtensionRunner(loaded.extensions, loaded.runtime, cwd, session, { registerProvider() {}, unregisterProvider() {} });
const activeTools = ['read', 'bash'];
runner.bindCore({
  sendMessage: () => {},
  sendUserMessage: () => {},
  appendEntry: (customType, data) => session.appendCustomEntry(customType, data),
  setSessionName: (name) => session.appendSessionInfo(name),
  getSessionName: () => session.getSessionName(),
  setLabel: (entryId, label) => session.appendLabelChange(entryId, label),
  getActiveTools: () => activeTools,
  getAllTools: () => ['read', 'bash', 'residual_tool'],
  setActiveTools: (names) => { activeTools.splice(0, activeTools.length, ...names); },
  refreshTools: () => {},
  getCommands: () => runner.getRegisteredCommands(),
  setModel: async () => {},
  getThinkingLevel: () => 'off',
  setThinkingLevel: () => {}
}, {
  getModel: () => undefined,
  isIdle: () => true,
  abort: () => {},
  hasPendingMessages: () => false,
  shutdown: () => {},
  getContextUsage: () => ({ tokens: 42, contextWindow: 1000, percent: 4.2 }),
  compact: () => {},
  getSystemPrompt: () => 'system prompt'
});
runner.bindCommandContext({
  waitForIdle: async () => {},
  newSession: async () => ({ cancelled: false }),
  fork: async () => ({ cancelled: false }),
  navigateTree: async () => ({ cancelled: false }),
  switchSession: async () => ({ cancelled: false }),
  reload: async () => {}
});

const providerPayload = await runner.emitBeforeProviderRequest({ messages: [{ role: 'user', content: 'hi' }] });
const contextMessages = await runner.emitContext([{ role: 'user', content: [{ type: 'text', text: 'hi' }] }]);
const beforeAgentStart = await runner.emitBeforeAgentStart('prompt residual', undefined, 'system prompt');
const toolCallRead = await runner.emitToolCall({ type: 'tool_call', toolCallId: 'tc-read', toolName: 'read', input: { path: 'README.md' } });
const toolCallWrite = await runner.emitToolCall({ type: 'tool_call', toolCallId: 'tc-write', toolName: 'write', input: { path: 'x', content: 'y' } });
const toolResult = await runner.emitToolResult({ type: 'tool_result', toolCallId: 'tc-read', toolName: 'read', input: { path: 'README.md' }, content: [{ type: 'text', text: 'original' }], details: undefined, isError: false });
const userBash = await runner.emit({ type: 'user_bash', command: 'echo residual', excludeFromContext: false, cwd });
const resourcesDiscover = await runner.emitResourcesDiscover(cwd, 'startup');
const sessionBeforeSwitch = await runner.emit({ type: 'session_before_switch', reason: 'resume', targetSessionFile: branchedSessionPath });
const sessionBeforeFork = await runner.emit({ type: 'session_before_fork', entryId: assistantEntryId });
const branchEntries = session.getBranch(compactionEntryId);
const sessionBeforeCompact = await runner.emit({ type: 'session_before_compact', preparation: { reason: 'manual' }, branchEntries, customInstructions: 'compact residual', signal: new AbortController().signal });
if (sessionBeforeCompact?.compaction) {
  const hookCompactionId = session.appendCompaction(sessionBeforeCompact.compaction.summary, sessionBeforeCompact.compaction.firstKeptEntryId, sessionBeforeCompact.compaction.tokensBefore, sessionBeforeCompact.compaction.details, true);
  const hookCompactionEntry = session.getEntry(hookCompactionId);
  await runner.emit({ type: 'session_compact', compactionEntry: hookCompactionEntry, fromExtension: true });
}
const treePrep = { targetId: userEntryId, oldLeafId: session.getLeafId(), commonAncestorId: userEntryId, entriesToSummarize: branchEntries, userWantsSummary: true, customInstructions: 'tree residual', label: 'user-label' };
const sessionBeforeTree = await runner.emit({ type: 'session_before_tree', preparation: treePrep, signal: new AbortController().signal });
await runner.emit({ type: 'session_tree', newLeafId: userEntryId, oldLeafId: treePrep.oldLeafId, summaryEntry: session.getEntry(branchSummaryEntryId), fromExtension: true });

const sessionFile = session.getSessionFile();
const sessionFileText = readFileSync(sessionFile, 'utf8');
const parsedSessionFile = parseSessionEntries(sessionFileText);
const loadedSessionFile = loadEntriesFromFile(sessionFile);
const branchedEntries = loadEntriesFromFile(branchedSessionPath);
const forkedEntries = loadEntriesFromFile(forked.getSessionFile());
const sessionContext = buildSessionContext(session.getEntries(), session.getLeafId());
const latestCompaction = sessionModule.getLatestCompactionEntry(session.getEntries());

const hash = (value) => 'sha256:' + createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
const result = {
  schema_version: 'pi.p5.residual.target_probe_output.v1',
  app: {
    name: configModule.APP_NAME,
    config_dir_name: configModule.CONFIG_DIR_NAME,
    version: configModule.VERSION,
    env_agent_dir: configModule.ENV_AGENT_DIR,
    sessions_dir: configModule.getSessionsDir()
  },
  cwd,
  agent_dir: agentDir,
  extension: {
    path: extensionPath,
    loaded_count: loaded.extensions.length,
    errors: loaded.errors,
    paths: runner.getExtensionPaths(),
    registered_tools: runner.getAllRegisteredTools().map((tool) => ({ name: tool.definition.name, label: tool.definition.label, promptSnippet: tool.definition.promptSnippet })),
    registered_commands: runner.getRegisteredCommands().map((command) => ({ name: command.name, description: command.description })),
    flags: Array.from(runner.getFlags().values()).map((flag) => ({ name: flag.name, type: flag.type, default: flag.default })),
    event_log: globalThis.__PI_P5_RESIDUAL_EVENTS ?? [],
    emitted_results: { providerPayload, contextMessages, beforeAgentStart, toolCallRead, toolCallWrite, toolResult, userBash, resourcesDiscover, sessionBeforeSwitch, sessionBeforeFork, sessionBeforeCompact, sessionBeforeTree }
  },
  session: {
    session_dir: sessionDir,
    session_file: sessionFile,
    session_id: session.getSessionId(),
    user_entry_id: userEntryId,
    assistant_entry_id: assistantEntryId,
    custom_entry_id: customEntryId,
    compaction_entry_id: compactionEntryId,
    branch_summary_entry_id: branchSummaryEntryId,
    branched_session_path: branchedSessionPath,
    forked_session_file: forked.getSessionFile(),
    continued_session_file: continued.getSessionFile(),
    reopened_session_id: reopened.getSessionId(),
    header: session.getHeader(),
    tree_root_count: session.getTree().length,
    leaf_id: session.getLeafId(),
    context_messages: sessionContext.messages,
    context_model: sessionContext.model,
    latest_compaction: latestCompaction,
    session_file_entry_types: parsedSessionFile.map((entry) => entry.type),
    loaded_entry_count: loadedSessionFile.length,
    branched_header: branchedEntries[0],
    branched_entry_types: branchedEntries.map((entry) => entry.type),
    forked_header: forkedEntries[0],
    forked_entry_types: forkedEntries.map((entry) => entry.type),
    hashes: {
      session_file: hash(sessionFileText),
      branched_session: hash(readFileSync(branchedSessionPath, 'utf8')),
      forked_session: hash(readFileSync(forked.getSessionFile(), 'utf8'))
    }
  },
  no_secret_env: {
    ANTHROPIC_API_KEY: Boolean(process.env.ANTHROPIC_API_KEY),
    OPENAI_API_KEY: Boolean(process.env.OPENAI_API_KEY),
    GEMINI_API_KEY: Boolean(process.env.GEMINI_API_KEY),
    PI_OFFLINE: process.env.PI_OFFLINE
  }
};
console.log('PI_P5_RESIDUAL_PROBE_JSON=' + JSON.stringify(result));
