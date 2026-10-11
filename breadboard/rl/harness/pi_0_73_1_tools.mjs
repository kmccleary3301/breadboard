import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { TOOL_IDS, createBashTool, createBashToolDefinition, createEditTool, createEditToolDefinition, createReadTool, createReadToolDefinition, createWriteTool, createWriteToolDefinition, fail, getShellConfig, getShellEnv, validateToolArguments, waitForChildProcess } from "./pi_0_73_1_runtime.mjs";













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

function createTrackedBashTool(cwd) {
  const operations = {
    exec(command, execCwd, { onData, signal, timeout, env }) {
      return new Promise((resolve, reject) => {
        if (!existsSync(execCwd)) {
          reject(new Error(`Working directory does not exist: ${execCwd}\nCannot execute bash commands.`));
          return;
        }
        const { shell, args } = getShellConfig();
        const child = spawn(shell, [...args, command], {
          cwd: execCwd,
          detached: process.platform !== "win32",
          env: env ?? getShellEnv(),
          stdio: ["ignore", "pipe", "pipe"],
        });
        const entry = trackBashProcess(child);
        let timedOut = false;
        let timeoutHandle;
        const onAbort = () => {
          if (entry) requestProcessGroupTermination(entry);
        };
        const cleanup = () => {
          clearTimeout(timeoutHandle);
          if (signal) signal.removeEventListener("abort", onAbort);
          if (entry) maybeForgetProcess(entry);
        };
        if (entry && timeout !== undefined && timeout > 0) {
          timeoutHandle = setTimeout(() => {
            timedOut = true;
            requestProcessGroupTermination(entry);
          }, timeout * 1000);
        }
        child.stdout?.on("data", onData);
        child.stderr?.on("data", onData);
        if (signal) {
          if (signal.aborted) onAbort();
          else signal.addEventListener("abort", onAbort, { once: true });
        }
        const done = waitForChildProcess(child)
          .then((code) => {
            cleanup();
            if (signal?.aborted) {
              reject(new Error("aborted"));
              return;
            }
            if (timedOut) {
              reject(new Error(`timeout:${timeout}`));
              return;
            }
            resolve({ exitCode: code });
          })
          .catch((error) => {
            cleanup();
            reject(error);
          });
        if (entry) entry.done = done;
      });
    },
  };
  return createBashTool(cwd, { operations });
}

async function waitForProcessGroupDead(entry) {
  if (!entry.leaderExited) return false;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (!processGroupAlive(entry)) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
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

const TOOL_FACTORIES = Object.freeze({
  bash: (cwd) => createTrackedBashTool(cwd),
  edit: (cwd) => createEditTool(cwd),
  read: (cwd) => createReadTool(cwd, { autoResizeImages: true }),
  write: (cwd) => createWriteTool(cwd),
});
const TOOL_DEFINITION_FACTORIES = Object.freeze({
  bash: (cwd) => createBashToolDefinition(cwd),
  edit: (cwd) => createEditToolDefinition(cwd),
  read: (cwd) => createReadToolDefinition(cwd, { autoResizeImages: true }),
  write: (cwd) => createWriteToolDefinition(cwd),
});

let nextBatchId = 1;

function validateCall(call, defaultCwd) {
  if (call === null || typeof call !== "object" || Array.isArray(call)) fail("call must be an object");
  const toolId = call.tool_id ?? call.toolId ?? call.name;
  if (typeof toolId !== "string" || !TOOL_IDS.has(toolId)) fail(`unknown tool_id: ${String(toolId)}`);
  const argumentsValue = call.arguments;
  const cwd = typeof call.cwd === "string" && call.cwd ? call.cwd : defaultCwd;
  const callIdValue = call.call_id ?? call.callId ?? call.id;
  const callId = typeof callIdValue === "string" && callIdValue ? callIdValue : "bb-native-call";
  return { toolId, argumentsValue, cwd, callId };
}

function prepareCall(call, defaultCwd) {
  const request = validateCall(call, defaultCwd);
  const tool = TOOL_FACTORIES[request.toolId](request.cwd);
  const prepared = typeof tool.prepareArguments === "function"
    ? tool.prepareArguments(request.argumentsValue)
    : request.argumentsValue;
  const argumentsValue = validateToolArguments(tool, {
    id: request.callId,
    name: request.toolId,
    arguments: prepared,
  });
  return { request, tool, argumentsValue };
}


async function executePrepared(prepared, signal) {
  try {
    const result = await prepared.tool.execute(prepared.request.callId, prepared.argumentsValue, signal);
    const details = result?.details && typeof result.details === "object" ? {...result.details} : {};
    delete details.effects;
    return {
      content: Array.isArray(result?.content) ? result.content : [],
      details,
      isError: false,
      terminate: false,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
      terminate: false,
    };
  }
}

async function executeCall(call, defaultCwd, signal) {
  try {
    return await executePrepared(prepareCall(call, defaultCwd), signal);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
      terminate: false,
    };
  }
}

function toolSchemas(workspace, advertisement) {
  return [...TOOL_IDS].map((name) => {
    const tool = TOOL_FACTORIES[name](workspace);
    const replacement = advertisement?.tools?.[name]?.description;
    const description = typeof replacement === "string" ? replacement : tool.description;
    return {
      type: "function",
      function: { name: tool.name, description, parameters: tool.parameters },
    };
  });
}

function originalDescriptionHashes(workspace) {
  return Object.fromEntries([...TOOL_IDS].map((name) => {
    const description = TOOL_FACTORIES[name](workspace).description ?? "";
    return [name, createHash("sha256").update(description, "utf8").digest("hex")];
  }));
}

function prepareTools(payload, defaultCwd) {
    if (!Array.isArray(payload?.calls)) fail("prepare_tools requires calls");
    const preparedInternal = [];
    const calls = [];
    const historyCalls = [];
    const errors = [];
    for (const call of payload.calls) {
      try {
        const value = prepareCall(call, defaultCwd);
        const item = {
          id: value.request.callId,
          name: value.request.toolId,
          arguments: value.argumentsValue,
        };
        preparedInternal.push({ ...item });
        calls.push({ ...item });
        // Pi keeps the sampled call in assistant history; only execution uses
        // the validator's converted argument clone.
        historyCalls.push({
          id: value.request.callId,
          name: value.request.toolId,
          arguments: value.request.argumentsValue,
        });
      } catch (error) {
        const message = String(error?.message ?? error);
        const item = {
          id: String(call?.call_id ?? call?.callId ?? call?.id ?? ""),
          name: String(call?.tool_id ?? call?.toolId ?? call?.name ?? ""),
          arguments: call?.arguments !== undefined ? call.arguments : {},
          error: message,
        };
        errors.push(message);
        preparedInternal.push({ ...item });
        calls.push(item);
        historyCalls.push({ id: item.id, name: item.name, arguments: item.arguments });
      }
    }
    const batchId = `pi-prepared-${nextBatchId++}`;
    toolState.retainedPreparedBatch = { batchId, prepared: preparedInternal };
    return {
      schema_version: "bb.pi-native.v1",
      kind: "prepared",
      calls,
      ...(errors.length ? { errors } : {}),
      history_calls: historyCalls,
    };

}

async function close(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length !== 0) {
      fail("close payload must be empty");
    }
    toolState.retainedPreparedBatch = null;
    const cleanup = await closeTrackedProcessGroups();
    return { schema_version: "bb.pi-native.v1", kind: "closed", cleanup };

}

async function executeBatch(payload, defaultCwd, signal) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || Object.keys(payload).length !== 0) {
      fail("execute_batch payload must be empty");
    }
    if (!toolState.retainedPreparedBatch) fail("execute_batch requires the retained prepared batch");
    let completionIndex = 0;
    const completedPreparationErrors = [];
    for (const [sourceIndex, call] of toolState.retainedPreparedBatch.prepared.entries()) {
      if (call.error) {
        completedPreparationErrors.push({
          sourceIndex,
          result: {
            id: call.id,
            completion_index: completionIndex++,
            content: [{ type: "text", text: call.error }],
            details: {},
            isError: true,
            terminate: false,
          },
        });
      }
    }
    const validCalls = toolState.retainedPreparedBatch.prepared
      .map((call, sourceIndex) => ({ call, sourceIndex }))
      .filter(({ call }) => !call.error);
    const completedValid = await Promise.all(validCalls.map(async ({ call, sourceIndex }) => {
      const request = validateCall(call, defaultCwd);
      const tool = TOOL_FACTORIES[request.toolId](request.cwd);
      const result = await executePrepared({ request, tool, argumentsValue: call.arguments }, signal);
      return {
        sourceIndex,
        result: { id: call.id, completion_index: completionIndex++, ...result },
      };
    }));
    const results = [...completedPreparationErrors, ...completedValid]
      .sort((left, right) => left.sourceIndex - right.sourceIndex)
      .map((entry) => entry.result);
    toolState.retainedPreparedBatch = null;
    return { schema_version: "bb.pi-native.v1", kind: "tool_results", results };

}





export { TOOL_DEFINITION_FACTORIES, close, executeBatch, executeCall, originalDescriptionHashes, prepareTools, toolSchemas, toolState };
