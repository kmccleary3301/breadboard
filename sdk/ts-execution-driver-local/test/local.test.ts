import test from "node:test"
import assert from "node:assert/strict"
import { setTimeout as sleep } from "node:timers/promises"

import {
  buildLocalProcessSandboxRequest,
  chooseTrustedLocalPlacement,
  defaultLocalCommandExecutor,
  executeLocalProcessSandboxRequest,
  makeTrustedLocalExecutionDriver,
  trustedLocalExecutionDriver,
  type LocalCommandExecutor,
} from "../src/index.js"

test("trusted local driver chooses inline vs local process cleanly", () => {
  assert.equal(
    chooseTrustedLocalPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-1",
      security_tier: "trusted_dev",
      isolation_class: "none",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    }),
    "inline_ts",
  )
  assert.equal(
    chooseTrustedLocalPlacement({
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-2",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    }),
    "local_process",
  )
})

test("trusted local driver can build a local-process sandbox request", () => {
  const request = buildLocalProcessSandboxRequest({
    requestId: "sandbox-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-3",
      security_tier: "trusted_dev",
      isolation_class: "process",
      allow_net_hosts: ["api.openai.com"],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    command: ["bash", "-lc", "echo hi"],
    workspaceRef: "workspace://repo/main",
  })
  assert.equal(request.placement_class, "local_process")
  assert.equal(request.command[0], "bash")
  assert.equal(trustedLocalExecutionDriver.supportsCapability({
    schema_version: "bb.execution_capability.v1",
    capability_id: "cap-3",
    security_tier: "trusted_dev",
    isolation_class: "process",
    secret_mode: "ref_only",
    evidence_mode: "replay_strict",
  }, "local_process"), true)
  const built = trustedLocalExecutionDriver.buildSandboxRequest?.({
    requestId: "sandbox-2",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-4",
      security_tier: "trusted_dev",
      isolation_class: "process",
      allow_net_hosts: [],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-4",
    },
    command: ["node", "-v"],
    workspaceRef: "workspace://repo/main",
  })
  assert.equal(built?.placement_class, "local_process")
})

test("trusted local driver can execute a local-process sandbox request", async () => {
  const request = buildLocalProcessSandboxRequest({
    requestId: "sandbox-exec-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-exec-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      allow_net_hosts: [],
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    command: ["node", "-e", "process.stdout.write('local ok')"],
    workspaceRef: "/tmp",
  })
  const result = await executeLocalProcessSandboxRequest(request)
  assert.equal(result.status, "completed")
  assert.ok(result.stdout_ref?.startsWith("file://"))
  assert.ok(result.side_effect_digest?.startsWith("sha256:"))
})

test("trusted local driver can manage a persistent terminal session lifecycle", async () => {
  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-1",
    command: [
      "node",
      "-e",
      [
        "process.stdout.write('ready\\n')",
        "process.stdin.on('data', (chunk) => {",
        "  process.stdout.write(`echo:${chunk.toString()}`)",
        "  process.exit(0)",
        "})",
      ].join("; "),
    ],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-term-1",
    },
    startupCallId: "call-start-1",
  })

  assert.ok(start)
  assert.equal(start?.descriptor.terminal_session_id, "term-local-1")

  await sleep(75)

  const firstPoll = await trustedLocalExecutionDriver.interactTerminalSession?.({
    terminalSessionId: "term-local-1",
    interactionKind: "poll",
  })
  assert.ok(firstPoll)
  assert.equal(firstPoll?.outputDeltas.length, 1)
  assert.equal(Buffer.from(firstPoll?.outputDeltas[0]?.chunk_b64 ?? "", "base64").toString("utf8"), "ready\n")

  const stdinResult = await trustedLocalExecutionDriver.interactTerminalSession?.({
    terminalSessionId: "term-local-1",
    interactionKind: "stdin",
    inputText: "hello\n",
    causingCallId: "call-continue-1",
    settleMs: 25,
  })
  assert.ok(stdinResult)
  assert.equal(stdinResult?.interaction.interaction_kind, "stdin")
  assert.equal(Buffer.from(stdinResult?.outputDeltas[0]?.chunk_b64 ?? "", "base64").toString("utf8"), "echo:hello\n")
  assert.equal(stdinResult?.end?.terminal_state, "completed")

  const registry = await trustedLocalExecutionDriver.snapshotTerminalRegistry?.()
  assert.equal(registry?.active_sessions.length, 0)
})

test("trusted local driver rejects interaction with an exited terminal session", async () => {
  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-exit-1",
    command: ["node", "-e", "process.stdout.write('done\\n')"],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-exit-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-exit-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-term-exit-1",
    },
    startupCallId: "call-exit-1",
  })
  assert.ok(start)

  await sleep(75)
  const polled = await trustedLocalExecutionDriver.interactTerminalSession?.({
    terminalSessionId: "term-local-exit-1",
    interactionKind: "poll",
    settleMs: 10,
  })
  assert.ok(polled?.end)
  assert.equal(polled?.end?.terminal_state, "completed")
  const snapshotAfterExit = await trustedLocalExecutionDriver.snapshotTerminalRegistry?.()
  assert.ok((snapshotAfterExit?.ended_session_ids ?? []).includes("term-local-exit-1"))

  await assert.rejects(
    () =>
      trustedLocalExecutionDriver.interactTerminalSession?.({
        terminalSessionId: "term-local-exit-1",
        interactionKind: "stdin",
        inputText: "late\n",
      }) ?? Promise.resolve(undefined),
    /Unknown terminal session/,
  )
})

test("trusted local driver cleanup is stable for missing or already cleaned sessions", async () => {
  const cleanedMissing = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-missing-1",
    scope: "single",
    sessionIds: ["term-local-missing-1"],
    signal: null,
  })
  assert.ok(cleanedMissing)
  assert.deepEqual(cleanedMissing?.cleaned_session_ids, [])
  assert.deepEqual(cleanedMissing?.failed_session_ids, ["term-local-missing-1"])

  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-cleanup-1",
    command: ["/bin/bash", "-lc", "sleep 5"],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-cleanup-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-cleanup-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-term-cleanup-1",
    },
    startupCallId: "call-cleanup-1",
  })
  assert.ok(start)

  const cleaned = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-present-1",
    scope: "single",
    sessionIds: ["term-local-cleanup-1"],
    signal: null,
  })
  assert.ok(cleaned)
  assert.deepEqual(cleaned?.cleaned_session_ids, ["term-local-cleanup-1"])
  const snapshotAfterCleanup = await trustedLocalExecutionDriver.snapshotTerminalRegistry?.()
  assert.ok((snapshotAfterCleanup?.ended_session_ids ?? []).includes("term-local-cleanup-1"))

  const cleanedAgain = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-present-2",
    scope: "single",
    sessionIds: ["term-local-cleanup-1"],
    signal: null,
  })
  assert.ok(cleanedAgain)
  assert.deepEqual(cleanedAgain?.cleaned_session_ids, ["term-local-cleanup-1"])
  assert.deepEqual(cleanedAgain?.failed_session_ids, [])
})

test("trusted local driver terminate triggers signal and awaits process exit", async () => {
  let processExited = false
  const customExecutor: LocalCommandExecutor = async ({ signal }) => {
    return new Promise((resolve) => {
      signal?.addEventListener("abort", () => {
        processExited = true
        resolve({
          exitCode: 130,
          stdout: "",
          stderr: "terminated",
        })
      })
    })
  }
  const driver = makeTrustedLocalExecutionDriver(customExecutor)
  const request = buildLocalProcessSandboxRequest({
    requestId: "req-term-verify",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-v",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
  })
  const execPromise = driver.execute!(request)
  assert.equal(processExited, false)
  await driver.terminate!(request, {
    reason: "deadline",
    signal: new AbortController().signal,
    deadlineAtMs: Date.now(),
  })
  assert.equal(processExited, true)
  const result = await execPromise
  assert.equal(result.status, "timed_out")
})

test("defaultLocalCommandExecutor terminates isolated process group including shell descendants on abort", async () => {
  const abortController = new AbortController()
  // Launch a process that writes its child pid to stdout, then sleeps
  const execPromise = defaultLocalCommandExecutor({
    command: [
      "node",
      "-e",
      "const { spawn } = require('node:child_process'); const c = spawn('sleep', ['60']); console.log(c.pid);",
    ],
    signal: abortController.signal,
  })

  // Give the process enough time to spawn descendant
  await sleep(100)
  abortController.abort(new Error("aborted"))
  const result = await execPromise
  assert.ok(result.exitCode !== 0)
  const descendantPid = parseInt(result.stdout.trim(), 10)
  if (!isNaN(descendantPid) && descendantPid > 0 && process.platform !== "win32") {
    // Wait briefly and verify descendant process is gone (sending signal 0 will throw ESRCH)
    await sleep(200)
    let isRunning = false
    try {
      process.kill(descendantPid, 0)
      isRunning = true
    } catch (err: unknown) {
      if ((err as NodeJS.ErrnoException).code === "ESRCH") {
        isRunning = false
      }
    }
    assert.equal(isRunning, false, "descendant process was successfully terminated")
  }
})

test("trusted local driver cleanup escalates to SIGKILL for SIGTERM-ignoring process and verifies exit", async () => {
  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-sigterm-ignore-1",
    command: [
      "node",
      "-e",
      "process.on('SIGTERM', () => {}); console.log('ready'); setInterval(() => {}, 1000);",
    ],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-ignore-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "replay_strict",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-ignore-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-term-ignore-1",
    },
    startupCallId: "call-ignore-1",
  })
  assert.ok(start)
  await sleep(100)

  const cleanupResult = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-ignore-1",
    scope: "single",
    sessionIds: ["term-local-sigterm-ignore-1"],
    signal: "SIGTERM",
  })

  assert.ok(cleanupResult)
  assert.deepEqual(cleanupResult?.cleaned_session_ids, ["term-local-sigterm-ignore-1"])
  assert.deepEqual(cleanupResult?.failed_session_ids, [])

  const registry = await trustedLocalExecutionDriver.snapshotTerminalRegistry?.()
  assert.ok(
    !registry?.active_sessions.some((s) => s.terminal_session_id === "term-local-sigterm-ignore-1"),
    "session was removed from active sessions after SIGKILL escalation",
  )
  assert.ok(
    (registry?.ended_session_ids ?? []).includes("term-local-sigterm-ignore-1"),
    "session is recorded in ended_session_ids",
  )
})

test("trusted local driver cleanup handles simulated windows platform without hanging or false failure", async () => {
  const originalPlatform = process.platform
  try {
    Object.defineProperty(process, "platform", { value: "win32" })
    const start = await trustedLocalExecutionDriver.startTerminalSession?.({
      terminalSessionId: "term-local-win32-1",
      command: ["node", "-e", "console.log('ready'); setInterval(() => {}, 1000);"],
      cwd: "/tmp",
      capability: {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-term-win32-1",
        security_tier: "trusted_dev",
        isolation_class: "process",
        secret_mode: "ref_only",
        evidence_mode: "minimal",
      },
      placement: {
        schema_version: "bb.execution_placement.v1",
        placement_id: "place-term-win32-1",
        placement_class: "local_process",
        runtime_id: "local",
        capability_id: "cap-term-win32-1",
      },
      startupCallId: "call-win32-1",
    })
    assert.ok(start)
    await sleep(50)

    const cleanupResult = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
      cleanupId: "cleanup-win32-1",
      scope: "single",
      sessionIds: ["term-local-win32-1"],
      signal: "SIGTERM",
    })

    assert.ok(cleanupResult)
    assert.deepEqual(cleanupResult?.cleaned_session_ids, ["term-local-win32-1"])
    assert.deepEqual(cleanupResult?.failed_session_ids, [])
  } finally {
    Object.defineProperty(process, "platform", { value: originalPlatform })
  }
})

test("trusted local driver terminate bounds settlement when executor ignores abort and never settles", async () => {
  const neverSettlingExecutor = () => new Promise<{ exitCode: number; stdout: string; stderr: string }>(() => {})
  const driver = makeTrustedLocalExecutionDriver(neverSettlingExecutor)

  const request = buildLocalProcessSandboxRequest({
    requestId: "req-local-never-settle",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-local-never-settle",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "100"],
  })

  const controller = new AbortController()
  const cap = {
    schema_version: "bb.execution_capability.v1" as const,
    capability_id: "cap-local-never-settle",
    security_tier: "trusted_dev" as const,
    isolation_class: "process" as const,
    secret_mode: "ref_only" as const,
    evidence_mode: "minimal" as const,
  }
  const place = {
    schema_version: "bb.execution_placement.v1" as const,
    placement_id: "place-local-never-settle",
    placement_class: "local_process" as const,
    runtime_id: "local",
    capability_id: cap.capability_id,
  }
  const executePromise = driver.execute!(request, {
    signal: controller.signal,
    deadlineAtMs: null,
    terminationGraceMs: 100,
    capability: cap,
    placement: place,
    driverId: "local-process",
  })

  // Terminate should complete boundedly despite neverSettlingExecutor
  const startMs = Date.now()
  await driver.terminate!(request, {
    reason: "cancelled",
    signal: controller.signal,
    deadlineAtMs: null,
  })
  const durationMs = Date.now() - startMs
  assert.ok(durationMs < 3000, `terminate resolved boundedly in ${durationMs}ms`)
})

test("trusted local driver cleanup terminates live descendants in process group when parent process exited first", async () => {
  if (process.platform === "win32") return

  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-orphan-descendant-1",
    command: [
      "node",
      "-e",
      "const { spawn } = require('node:child_process'); const c = spawn('sleep', ['60'], { stdio: 'ignore' }); console.log(c.pid); process.exit(0);",
    ],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-term-descendant-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-term-descendant-1",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-term-descendant-1",
    },
  })
  assert.ok(start)

  // Give the process enough time to spawn descendant and exit
  await sleep(100)

  // Read the descendant PID from the output delta
  const stdoutChunk = start.outputDeltas?.[0]?.chunk_b64
  let descendantPid = 0
  if (stdoutChunk) {
    descendantPid = parseInt(Buffer.from(stdoutChunk, "base64").toString("utf8").trim(), 10)
  }
  if (!descendantPid || isNaN(descendantPid)) {
    const interact = await trustedLocalExecutionDriver.interactTerminalSession?.({
      terminalSessionId: "term-local-orphan-descendant-1",
      interactionKind: "stdin",
      inputText: "",
    })
    for (const delta of interact?.outputDeltas ?? []) {
      const text = Buffer.from(delta.chunk_b64, "base64").toString("utf8").trim()
      const parsed = parseInt(text, 10)
      if (!isNaN(parsed) && parsed > 0) {
        descendantPid = parsed
        break
      }
    }
  }

  assert.ok(descendantPid > 0, "descendant pid was parsed")

  // Verify descendant process is currently running even though parent exited
  let isDescendantAlive = false
  try {
    process.kill(descendantPid, 0)
    isDescendantAlive = true
  } catch {
    isDescendantAlive = false
  }
  assert.equal(isDescendantAlive, true, "descendant is alive while parent has exited")

  // Cleanup should signal process group and terminate the descendant
  const cleanupResult = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "cleanup-descendant-1",
    scope: "single",
    sessionIds: ["term-local-orphan-descendant-1"],
    signal: "SIGTERM",
  })
  assert.ok(cleanupResult)
  assert.deepEqual(cleanupResult?.cleaned_session_ids, ["term-local-orphan-descendant-1"])

  // Verify descendant process is dead
  await sleep(100)
  let isDescendantStillAlive = true
  try {
    process.kill(descendantPid, 0)
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === "ESRCH") {
      isDescendantStillAlive = false
    }
  }
  assert.equal(isDescendantStillAlive, false, "descendant process was terminated by process-group cleanup")
})

test("trusted local driver cleanup handles process tree termination on Windows preserving graceful signal semantics before force-kill", async () => {
  const originalPlatform = process.platform
  Object.defineProperty(process, "platform", { value: "win32", configurable: true })
  try {
    const start = await trustedLocalExecutionDriver.startTerminalSession?.({
      terminalSessionId: "term-win-test-1",
      command: ["node", "-e", "console.log('hi'); process.exit(0);"],
      capability: {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-win-1",
        security_tier: "trusted_dev",
        isolation_class: "process",
        secret_mode: "ref_only",
        evidence_mode: "minimal",
      },
      placement: {
        schema_version: "bb.execution_placement.v1",
        placement_id: "place-win-1",
        placement_class: "local_process",
        runtime_id: "local",
        capability_id: "cap-win-1",
      },
    })
    assert.ok(start)
    const cleanup = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
      cleanupId: "cleanup-win-test-1",
      scope: "single",
      sessionIds: ["term-win-test-1"],
      signal: "SIGTERM",
    })
    assert.ok(cleanup)
    assert.deepEqual(cleanup?.cleaned_session_ids, ["term-win-test-1"])
    assert.deepEqual(cleanup?.failed_session_ids, [])
  } finally {
    Object.defineProperty(process, "platform", { value: originalPlatform, configurable: true })
  }
})
