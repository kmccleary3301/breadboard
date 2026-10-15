import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { PHASE_SCHEMA_VERSION, TOOL_ORDER, createBashTool, createEditTool, createFindTool, createGrepTool, createLsTool, createReadTool, createWriteTool, fail, getShellConfig, getShellEnv, requireExactKeys, validateToolArguments } from "./pi_0_57_1_runtime.mjs";











const toolState = { retainedPreparedBatch: null };
const trackedProcessGroups = new Set();

function processGroupAlive(entry) {
  if (entry.pgid === null) return false;
  try {
    process.kill(-entry.pgid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function signalProcessGroup(entry) {
  if (entry.pgid === null) return false;
  try {
    process.kill(-entry.pgid, "SIGKILL");
    entry.signalSent = true;
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    return false;
  }
}

function markLeaderExit(entry) {
  if (entry.leaderExited) return;
  entry.leaderExited = true;
  entry.groupAliveAtExit = processGroupAlive(entry);
  if (entry.groupAliveAtExit) entry.reportable = true;
}

function trackBashProcess(child) {
  if (!child.pid) return null;
  const entry = {
    child,
    pid: child.pid,
    pgid: process.platform === "win32" ? null : child.pid,
    done: null,
    leaderExited: false,
    groupAliveAtExit: false,
    reportable: false,
    signalSent: false,
  };
  trackedProcessGroups.add(entry);
  child.once("exit", () => markLeaderExit(entry));
  return entry;
}

function maybeForgetProcess(entry) {
  if (entry.leaderExited && !entry.groupAliveAtExit) trackedProcessGroups.delete(entry);
}

function requestProcessGroupTermination(entry) {
  if (entry.leaderExited) {
    if (entry.groupAliveAtExit && !entry.signalSent && processGroupAlive(entry)) {
      signalProcessGroup(entry);
    }
    return;
  }
  entry.reportable = true;
  signalProcessGroup(entry);
}

/**
 * Pinned bash tool operations (createBashTool options.operations,
 * core/tools/bash.js:104-107) with process-group tracking.  The body mirrors
 * pinned defaultBashOperations (bash.js:23-95): same shell resolution order,
 * detached spawn, `close`-based settlement, and rejection messages; the
 * tree kill (killProcessTree -> SIGKILL -pid, utils/shell.js:157-185) goes
 * through the tracked group so close() can prove every group is dead.
 */
function createTrackedBashOperations() {
  return {
    exec(command, execCwd, { onData, signal, timeout, env }) {
      return new Promise((resolvePromise, reject) => {
        const { shell, args } = getShellConfig();
        if (!existsSync(execCwd)) {
          reject(new Error(`Working directory does not exist: ${execCwd}\nCannot execute bash commands.`));
          return;
        }
        const child = spawn(shell, [...args, command], {
          cwd: execCwd,
          detached: true,
          env: env ?? getShellEnv(),
          stdio: ["ignore", "pipe", "pipe"],
        });
        const entry = trackBashProcess(child);
        let settleDone;
        if (entry) entry.done = new Promise((resolveDone) => { settleDone = resolveDone; });
        let timedOut = false;
        let timeoutHandle;
        const onAbort = () => {
          if (entry) requestProcessGroupTermination(entry);
        };
        const cleanup = () => {
          if (timeoutHandle) clearTimeout(timeoutHandle);
          if (signal) signal.removeEventListener("abort", onAbort);
          if (entry) {
            maybeForgetProcess(entry);
            settleDone();
          }
        };
        if (timeout !== undefined && timeout > 0) {
          timeoutHandle = setTimeout(() => {
            timedOut = true;
            if (entry) requestProcessGroupTermination(entry);
          }, timeout * 1000);
        }
        if (child.stdout) child.stdout.on("data", onData);
        if (child.stderr) child.stderr.on("data", onData);
        child.on("error", (error) => {
          cleanup();
          reject(error);
        });
        if (signal) {
          if (signal.aborted) onAbort();
          else signal.addEventListener("abort", onAbort, { once: true });
        }
        child.on("close", (code) => {
          cleanup();
          if (signal?.aborted) {
            reject(new Error("aborted"));
            return;
          }
          if (timedOut) {
            reject(new Error(`timeout:${timeout}`));
            return;
          }
          resolvePromise({ exitCode: code });
        });
      });
    },
  };
}

async function waitForProcessGroupDead(entry) {
  if (!entry.leaderExited) return false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (!processGroupAlive(entry)) return true;
    await new Promise((resolveWait) => setTimeout(resolveWait, 10));
  }
  return !processGroupAlive(entry);
}

async function closeTrackedProcessGroups() {
  const entries = [...trackedProcessGroups];
  for (const entry of entries) requestProcessGroupTermination(entry);
  await Promise.allSettled(entries.map((entry) => entry.done));
  const processes = [];
  for (const entry of entries) {
    if (!entry.reportable) {
      trackedProcessGroups.delete(entry);
      continue;
    }
    const dead = await waitForProcessGroupDead(entry);
    processes.push({ pid: entry.pid, pgid: entry.pgid, dead });
    if (dead) trackedProcessGroups.delete(entry);
  }
  return { processes, all_dead: processes.every(({ dead }) => dead) };
}

/**
 * Tool registry as pinned AgentSession._buildRuntime builds it
 * (agent-session.js:1746-1755: createAllTools(cwd, {read: {autoResizeImages},
 * bash: {commandPrefix}}), tools/index.js:48-58), with the bash tool's
 * operations hook tracking process groups.
 */
function createToolRegistry(cwd, settingsManager) {
  const autoResizeImages = settingsManager.getImageAutoResize();
  const commandPrefix = settingsManager.getShellCommandPrefix();
  const tools = {
    read: createReadTool(cwd, { autoResizeImages }),
    bash: createBashTool(cwd, { commandPrefix, operations: createTrackedBashOperations() }),
    edit: createEditTool(cwd),
    write: createWriteTool(cwd),
    grep: createGrepTool(cwd),
    find: createFindTool(cwd),
    ls: createLsTool(cwd),
  };
  return TOOL_ORDER.map((name) => {
    const tool = tools[name];
    if (tool.name !== name) fail(`pinned tool factory ${name} produced ${tool.name}`);
    return tool;
  });
}

function errorText(error) {
  // agent-loop.js:237-240
  return error instanceof Error ? error.message : String(error);
}

function prepareTools(initializedState, payload) {
  if (!initializedState) fail("prepare_tools requires initialize");
  requireExactKeys(payload, ["calls"], "prepare_tools payload");
  if (!Array.isArray(payload.calls)) fail("prepare_tools requires calls");
  const prepared = [];
  const calls = [];
  const errors = [];
  for (const call of payload.calls) {
    requireExactKeys(call, ["id", "name", "arguments"], "tool call");
    if (typeof call.id !== "string" || !call.id || typeof call.name !== "string") {
      fail("tool call id and name must be strings");
    }
    const toolCall = { type: "toolCall", id: call.id, name: call.name, arguments: call.arguments };
    try {
      // agent-loop.js:213 lookup and :223-225 not-found/validation, in order.
      const tool = initializedState.tools.find((candidate) => candidate.name === toolCall.name);
      if (!tool) throw new Error(`Tool ${toolCall.name} not found`);
      const argumentsValue = validateToolArguments(tool, toolCall);
      prepared.push({ id: call.id, tool, argumentsValue });
      calls.push({ id: call.id, name: call.name, arguments: argumentsValue });
    } catch (error) {
      const message = errorText(error);
      errors.push(message);
      prepared.push({ id: call.id, error: message });
      calls.push({ id: call.id, name: call.name, arguments: call.arguments, error: message });
    }
  }
  toolState.retainedPreparedBatch = prepared;
  return {
    schema_version: PHASE_SCHEMA_VERSION,
    kind: "prepared",
    calls,
    ...(errors.length ? { errors } : {}),
  };
}

async function executeBatch(payload, signal) {
  requireExactKeys(payload, [], "execute_batch payload");
  if (!toolState.retainedPreparedBatch) fail("execute_batch requires the retained prepared batch");
  const prepared = toolState.retainedPreparedBatch;
  toolState.retainedPreparedBatch = null;
  const results = [];
  // agent-loop.js:207-276: one call at a time, in source order.
  for (const [index, call] of prepared.entries()) {
    let result;
    let isError = false;
    if (call.error !== undefined) {
      result = { content: [{ type: "text", text: call.error }], details: {} };
      isError = true;
    } else {
      try {
        // agent-loop.js:226-234 passes an update callback; updates are not
        // model-visible, so they are dropped here.
        result = await call.tool.execute(call.id, call.argumentsValue, signal, () => {});
      } catch (error) {
        result = { content: [{ type: "text", text: errorText(error) }], details: {} };
        isError = true;
      }
    }
    // agent-loop.js:250-258 copies result.details (:255); an undefined value is
    // absent from the serialized toolResult.
    results.push({
      id: call.id,
      completion_index: index,
      content: result.content,
      ...(result.details !== undefined ? { details: result.details } : {}),
      isError,
      terminate: false,
    });
  }
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "tool_results", results };
}

async function close(payload) {
  requireExactKeys(payload, [], "close payload");
  toolState.retainedPreparedBatch = null;
  const cleanup = await closeTrackedProcessGroups();
  return { schema_version: PHASE_SCHEMA_VERSION, kind: "closed", cleanup };
}





export { close, createToolRegistry, executeBatch, prepareTools, toolState };
