import fs from "node:fs"
import { spawn } from "node:child_process"
import test from "node:test"
import assert from "node:assert/strict"
import { setTimeout as sleep } from "node:timers/promises"
import {
  buildLocalProcessSandboxRequest,
  chooseTrustedLocalPlacement,
  defaultLocalCommandExecutor,
  executeLocalProcessSandboxRequest,
  LocalTerminalSessionManager,
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

test("trusted local direct execution classifies an ordinary abort as cancelled", async () => {
  const request = buildLocalProcessSandboxRequest({
    requestId: "sandbox-cancelled-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-cancelled-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    workspaceRef: "/tmp",
  })
  const controller = new AbortController()
  controller.abort()
  let executorCalls = 0
  const result = await executeLocalProcessSandboxRequest(request, {
    signal: controller.signal,
    commandExecutor: async () => {
      executorCalls++
      return { exitCode: 143, stdout: "", stderr: "" }
    },
  })
  assert.equal(result.status, "cancelled")
  assert.equal(result.error?.reason, "execution_cancelled")
  assert.equal(executorCalls, 0)
})
test("trusted local direct execution classifies an already-deadline-aborted request without invoking executor", async () => {
  const request = buildLocalProcessSandboxRequest({
    requestId: "sandbox-deadline-pre-abort-1",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-deadline-pre-abort-1",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    command: ["sleep", "60"],
    workspaceRef: "/tmp",
  })
  const controller = new AbortController()
  controller.abort("deadline")
  let executorCalls = 0
  const result = await executeLocalProcessSandboxRequest(request, {
    signal: controller.signal,
    commandExecutor: async () => {
      executorCalls++
      return { exitCode: 0, stdout: "unexpected", stderr: "" }
    },
  })
  assert.equal(result.status, "timed_out")
  assert.equal(result.error?.reason, "deadline_exceeded")
  assert.ok(result.stdout_ref?.startsWith("file://"))
  assert.ok(result.stderr_ref?.startsWith("file://"))
  assert.ok(result.side_effect_digest?.startsWith("sha256:"))
  assert.equal(executorCalls, 0)
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


test("trusted local snapshot excludes an exited session before poll delivery", async () => {
  const manager = new LocalTerminalSessionManager()
  await manager.startSession({
    terminalSessionId: "term-local-snapshot-exit",
    command: ["node", "-e", "process.exit(0)"],
    cwd: "/tmp",
  })
  await sleep(75)

  const snapshot = await manager.snapshotRegistry()

  assert.equal(
    snapshot.active_sessions.some(
      (session) => session.terminal_session_id === "term-local-snapshot-exit",
    ),
    false,
  )
  assert.ok((snapshot.ended_session_ids ?? []).includes("term-local-snapshot-exit"))
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

  // Terminate must reject when the injected executor never settles.
  const startMs = Date.now()
  await assert.rejects(
    async () =>
      driver.terminate!(request, {
        reason: "cancelled",
        signal: controller.signal,
        deadlineAtMs: null,
      }),
  )
  const durationMs = Date.now() - startMs
  assert.ok(durationMs >= 1900 && durationMs < 3000, `terminate timed out in ${durationMs}ms`)
})

test("trusted local driver cleanup terminates live descendants in process group when parent process exited first", async () => {
  if (process.platform === "win32") return

  const pidFile = `/tmp/bb-test-orphan-${Date.now()}-${Math.random().toString(36).slice(2)}.pid`
  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-orphan-descendant-1",
    command: [
      "node",
      "-e",
      `const { spawn } = require('node:child_process'); const fs = require('node:fs'); const c = spawn('sleep', ['60'], { stdio: 'ignore' }); fs.writeFileSync('${pidFile}', String(c.pid)); process.exit(0);`,
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

  // Wait for pid file to be written
  let descendantPid = 0
  for (let i = 0; i < 20; i++) {
    if (fs.existsSync(pidFile)) {
      const content = fs.readFileSync(pidFile, "utf8").trim()
      const parsed = parseInt(content, 10)
      if (!isNaN(parsed) && parsed > 0) {
        descendantPid = parsed
        break
      }
    }
    await sleep(25)
  }
  try {
    fs.unlinkSync(pidFile)
  } catch {}

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


test("trusted local driver rejects start for duplicate active terminal session ID", async () => {
  const start1 = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-local-active-dup",
    command: ["node", "-e", "setInterval(() => {}, 1000)"],
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-local-dup",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-local-dup",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-local-dup",
    },
  })
  assert.ok(start1)

  try {
    await assert.rejects(
      () =>
        trustedLocalExecutionDriver.startTerminalSession?.({
          terminalSessionId: "term-local-active-dup",
          command: ["node", "-e", "console.log('hi')"],
        }) ?? Promise.resolve(undefined as any),
      /Terminal session already active/,
    )
  } finally {
    await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
      cleanupId: "cleanup-local-dup",
      scope: "single",
      sessionIds: ["term-local-active-dup"],
    })
  }
})

test("trusted local manager drops PGID when group is gone and never signals reused PGID on idempotent cleanup", async () => {
  let aliveCheckCount = 0
  const killedCalls: Array<{ pgid: number | null | undefined; signal: NodeJS.Signals }> = []
  let simulateGroupAlive = true

  const manager = new LocalTerminalSessionManager({
    isGroupAlive: (pgid: number | null | undefined) => {
      aliveCheckCount++
      return simulateGroupAlive
    },
    killProcessTree: (child: unknown, pgid: number | null | undefined, signal: NodeJS.Signals) => {
      killedCalls.push({ pgid, signal })
      if (child && typeof (child as any).kill === "function") {
        try {
          ;(child as any).kill("SIGKILL")
        } catch {}
      }
      // When killed, simulate group exiting
      simulateGroupAlive = false
    },
  })

  // Start session
  await manager.startSession({
    terminalSessionId: "term-reused-pgid-1",
    command: ["node", "-e", "setInterval(() => {}, 1000)"],
  })

  // First cleanup - terminates the active session group
  const cleanup1 = await manager.cleanupSessions({
    cleanupId: "clean-1",
    scope: "single",
    sessionIds: ["term-reused-pgid-1"],
  })
  assert.deepEqual(cleanup1.cleaned_session_ids, ["term-reused-pgid-1"])
  assert.ok(killedCalls.length > 0, "killProcessTree was called on active session")

  const killCountAfterFirstCleanup = killedCalls.length

  // Now simulate OS reusing that exact PGID for an unrelated process group
  simulateGroupAlive = true

  // Idempotent second cleanup on already ended session
  const cleanup2 = await manager.cleanupSessions({
    cleanupId: "clean-2",
    scope: "single",
    sessionIds: ["term-reused-pgid-1"],
  })
  assert.deepEqual(cleanup2.cleaned_session_ids, ["term-reused-pgid-1"])

  // Verify killProcessTree was NEVER called during second cleanup because PGID was dropped
  assert.equal(
    killedCalls.length,
    killCountAfterFirstCleanup,
    "killProcessTree was NOT called on reused PGID during idempotent cleanup",
  )
})

test("trusted local driver cleanup with scope all cleans parent-exited session with live descendant", async () => {
  if (process.platform === "win32") return

  const pidFile = `/tmp/bb-test-orphan-all-${Date.now()}-${Math.random().toString(36).slice(2)}.pid`
  const start = await trustedLocalExecutionDriver.startTerminalSession?.({
    terminalSessionId: "term-orphan-all-1",
    command: [
      "node",
      "-e",
      `const { spawn } = require('node:child_process'); const fs = require('node:fs'); const c = spawn('sleep', ['60'], { stdio: 'ignore' }); fs.writeFileSync('${pidFile}', String(c.pid)); process.exit(0);`,
    ],
    cwd: "/tmp",
    capability: {
      schema_version: "bb.execution_capability.v1",
      capability_id: "cap-orphan-all",
      security_tier: "trusted_dev",
      isolation_class: "process",
      secret_mode: "ref_only",
      evidence_mode: "minimal",
    },
    placement: {
      schema_version: "bb.execution_placement.v1",
      placement_id: "place-orphan-all",
      placement_class: "local_process",
      runtime_id: "local",
      capability_id: "cap-orphan-all",
    },
  })
  assert.ok(start)

  // Wait for pid file
  let descendantPid = 0
  for (let i = 0; i < 20; i++) {
    if (fs.existsSync(pidFile)) {
      const content = fs.readFileSync(pidFile, "utf8").trim()
      const parsed = parseInt(content, 10)
      if (!isNaN(parsed) && parsed > 0) {
        descendantPid = parsed
        break
      }
    }
    await sleep(25)
  }
  try {
    fs.unlinkSync(pidFile)
  } catch {}

  assert.ok(descendantPid > 0, "descendant pid was parsed")

  // Poll interaction to deliver end of parent process, moving session to endedSessionIds while descendant is live
  await trustedLocalExecutionDriver.interactTerminalSession?.({
    terminalSessionId: "term-orphan-all-1",
    interactionKind: "poll",
    settleMs: 50,
  })

  // Verify descendant is alive
  let isAlive = false
  try {
    process.kill(descendantPid, 0)
    isAlive = true
  } catch {
    isAlive = false
  }
  assert.equal(isAlive, true, "descendant is alive")

  // Global cleanup with scope: "all"
  const cleanupResult = await trustedLocalExecutionDriver.cleanupTerminalSessions?.({
    cleanupId: "clean-all-local",
    scope: "all",
    signal: "SIGTERM",
  })
  assert.ok(cleanupResult)
  assert.ok(cleanupResult?.cleaned_session_ids.includes("term-orphan-all-1"))

  // Verify descendant process was terminated
  await sleep(100)
  let isStillAlive = true
  try {
    process.kill(descendantPid, 0)
  } catch (err: unknown) {
    if ((err as NodeJS.ErrnoException).code === "ESRCH") {
      isStillAlive = false
    }
  }
  assert.equal(isStillAlive, false, "descendant was terminated by global cleanup")
})

test("trusted local driver rejects local terminal sessions on Windows", async () => {
  const originalPlatform = process.platform
  Object.defineProperty(process, "platform", { value: "win32", configurable: true })
  try {
    const supports = trustedLocalExecutionDriver.supportsTerminalSessions?.(
      {
        schema_version: "bb.execution_capability.v1",
        capability_id: "cap-win",
        security_tier: "trusted_dev",
        isolation_class: "process",
        secret_mode: "ref_only",
        evidence_mode: "minimal",
      },
      "local_process",
    )
    assert.equal(supports, false, "supportsTerminalSessions returns false on Windows")

    await assert.rejects(
      () =>
        trustedLocalExecutionDriver.startTerminalSession?.({
          terminalSessionId: "term-win-reject",
          command: ["node", "-e", "console.log('hi')"],
        }) ?? Promise.resolve(undefined as any),
      /not supported on Windows/,
    )
  } finally {
    Object.defineProperty(process, "platform", { value: originalPlatform, configurable: true })
  }
})

test("trusted local manager retains >32 ended sessions with live descendants without eviction", async () => {
  const livePgids = new Set<number>()
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: (pgid: number | null | undefined) => (pgid != null ? livePgids.has(pgid) : false),
    isProcessAlive: () => false,
    getProcessStartToken: () => null,
    killProcessTree: (child: unknown, pgid: number | null | undefined, signal: NodeJS.Signals) => {
      if (pgid != null) {
        livePgids.delete(pgid)
      }
    },
  })
  for (let i = 1; i <= 40; i++) {
    livePgids.add(1000 + i)
  }

  // Remember 40 ended sessions each with a live group and captured identity
  for (let i = 1; i <= 40; i++) {
    ;(manager as any).rememberEndedSession(`term-descendant-${i}`, 1000 + i, {
      sessionId: `term-descendant-${i}`,
      leaderPid: 1000 + i,
      startToken: `start-token-${i}`,
      startedAtMs: Date.now() - 10000,
      command: ["node", "-e", "process.exit(0)"],
    })
  }
  // Cleanup scope all must target all 40 un-evicted live-descendant sessions
  const cleanupResult = await manager.cleanupSessions({
    cleanupId: "clean-40-descendants",
    scope: "all",
  })

  assert.equal(cleanupResult.cleaned_session_ids.length, 40)
  for (let i = 1; i <= 40; i++) {
    assert.ok(cleanupResult.cleaned_session_ids.includes(`term-descendant-${i}`))
  }
  assert.equal(livePgids.size, 0, "all 40 live descendant groups were killed and cleaned")
})

test("trusted local driver cleanup does not call probeKillTree with PGID when probeGroupAlive is false", async () => {
  const killedPgids: Array<number | null | undefined> = []
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: () => false, // Group is already dead
    killProcessTree: (child, pgid) => {
      killedPgids.push(pgid)
    },
  })

  // Start session
  await manager.startSession({
    terminalSessionId: "term-false-probe-1",
    command: ["node", "-e", "process.exit(0)"],
  })

  // Cleanup active session where probeGroupAlive is false
  const cleanupResult = await manager.cleanupSessions({
    cleanupId: "clean-false-probe",
    scope: "single",
    sessionIds: ["term-false-probe-1"],
  })

  assert.deepEqual(cleanupResult.cleaned_session_ids, ["term-false-probe-1"])
  // Verify probeKillTree was NEVER called with a PGID
  assert.equal(killedPgids.length, 0, "probeKillTree was not called when probeGroupAlive returned false")
})

test("trusted local driver rejects start reuse of session ID with uncleaned ended descendant PGID", async () => {
  let groupAlive = true
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: () => groupAlive,
    killProcessTree: () => {
      groupAlive = false
    },
  })

  // Start session
  await manager.startSession({
    terminalSessionId: "term-reuse-descendant-1",
    command: ["node", "-e", "process.exit(0)"],
  })
  await sleep(60)

  // Simulate parent exit with live descendant by verifying state
  await assert.rejects(
    async () => {
      await manager.startSession({
        terminalSessionId: "term-reuse-descendant-1",
        command: ["node", "-e", "process.exit(0)"],
      })
    },
    /Terminal session already active or pending cleanup: term-reuse-descendant-1/,
  )

  // Cleanup the session
  const cleanRes = await manager.cleanupSessions({
    cleanupId: "clean-reuse-1",
    scope: "single",
    sessionIds: ["term-reuse-descendant-1"],
  })
  assert.deepEqual(cleanRes.cleaned_session_ids, ["term-reuse-descendant-1"])

  // Now starting with the same ID succeeds because ownership was properly cleared
  const restarted = await manager.startSession({
    terminalSessionId: "term-reuse-descendant-1",
    command: ["node", "-e", "process.exit(0)"],
  })
  assert.ok(restarted.descriptor)
  assert.equal(restarted.descriptor.terminal_session_id, "term-reuse-descendant-1")
})

test("trusted local manager retains and cleans >32 immediate inert ended sessions without cap failure", async () => {
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: () => false,
  })

  // Start and immediate-exit 40 sessions
  for (let i = 1; i <= 40; i++) {
    await manager.startSession({
      terminalSessionId: `term-inert-40-${i}`,
      command: ["node", "-e", "process.exit(0)"],
    })
    await manager.interactSession({
      terminalSessionId: `term-inert-40-${i}`,
      interactionKind: "poll",
    })
  }

  // Cleanup scope all must clean all 40 inert ended sessions
  const cleanupResult = await manager.cleanupSessions({
    cleanupId: "clean-40-inert",
    scope: "all",
  })

  assert.equal(cleanupResult.cleaned_session_ids.length, 40)
  assert.deepEqual(cleanupResult.failed_session_ids, [])
  for (let i = 1; i <= 40; i++) {
    assert.ok(cleanupResult.cleaned_session_ids.includes(`term-inert-40-${i}`))
  }
})

test("trusted local manager refuses to kill reused PGID when identity cannot be validated", async () => {
  const killedPgids: Array<number | null | undefined> = []
  let groupAlive = true
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: (pgid) => (pgid === 9999 ? groupAlive : false),
    killProcessTree: (_child, pgid) => {
      killedPgids.push(pgid)
    },
    validateGroupOwnership: (_identity, _pgid) => {
      // Refuse ownership because PGID 9999 belongs to an unrelated new process
      return false
    },
  })

  // Simulate ended session with PGID 9999
  ;(manager as any).rememberEndedSession("term-reused-pgid", 9999, {
    sessionId: "term-reused-pgid",
    leaderPid: 9999,
    startToken: null,
    startedAtMs: Date.now() - 10000,
    command: ["node", "-e", "process.exit(0)"],
  })

  // Cleanup targeting the session (Call 1)
  const cleanRes1 = await manager.cleanupSessions({
    cleanupId: "clean-reused-pgid-1",
    scope: "single",
    sessionIds: ["term-reused-pgid"],
  })
  assert.equal(killedPgids.length, 0, "unrelated process group 9999 was protected from false kill signal")
  assert.deepEqual(cleanRes1.failed_session_ids, ["term-reused-pgid"], "call 1 reported failed/unconfirmed to protect reused group")

  // Two-call regression: Call 2 MUST still report failed while the unconfirmed group is alive (never falsely downgraded to cleaned)
  const cleanRes2 = await manager.cleanupSessions({
    cleanupId: "clean-reused-pgid-2",
    scope: "single",
    sessionIds: ["term-reused-pgid"],
  })
  assert.equal(killedPgids.length, 0, "unrelated process group 9999 was still protected")
  assert.deepEqual(cleanRes2.failed_session_ids, ["term-reused-pgid"], "call 2 still reported failed/unconfirmed")

  // Call 3: Once the group is positively inert on the system, cleanup safely reports cleaned
  groupAlive = false
  const cleanRes3 = await manager.cleanupSessions({
    cleanupId: "clean-reused-pgid-3",
    scope: "single",
    sessionIds: ["term-reused-pgid"],
  })
  assert.deepEqual(cleanRes3.cleaned_session_ids, ["term-reused-pgid"], "call 3 reported cleaned once positively inert")
})

test("trusted local manager same-ID successful restart removes ID from ended_session_ids in registry snapshot", async () => {
  const manager = new LocalTerminalSessionManager({
    isGroupAlive: () => false,
  })

  // 1. Start and end session
  await manager.startSession({
    terminalSessionId: "term-local-restart-id",
    command: ["node", "-e", "process.exit(0)"],
  })
  await sleep(60)
  await manager.interactSession({
    terminalSessionId: "term-local-restart-id",
    interactionKind: "poll",
  })
  let snap = await manager.snapshotRegistry()
  assert.equal(snap.active_sessions.length, 0)
  assert.deepEqual(snap.ended_session_ids, ["term-local-restart-id"])

  // 2. Restart with same ID
  const restartRes = await manager.startSession({
    terminalSessionId: "term-local-restart-id",
    command: ["node", "-e", "setTimeout(() => {}, 1000)"],
  })
  assert.ok(restartRes.descriptor)

  // 3. Registry must show exactly 1 active and 0 ended for that ID (never 2 total for one ID)
  snap = await manager.snapshotRegistry()
  assert.equal(snap.active_sessions.length, 1)
  assert.equal(snap.active_sessions[0]?.terminal_session_id, "term-local-restart-id")
  assert.equal(Boolean(snap.ended_session_ids?.includes("term-local-restart-id")), false)
  await manager.cleanupSessions({
    cleanupId: "cleanup-restarted-local-id",
    scope: "single",
    sessionIds: ["term-local-restart-id"],
  })
})

test("shared default local manager detects PID reuse and protects unrelated process without injected validator", async () => {
  // Use default manager (no validateGroupOwnership option provided)
  // Spawn an unrelated process whose PID is alive
  const unrelatedChild = spawn("node", ["-e", "setInterval(() => {}, 1000)"])
  const unrelatedPid = unrelatedChild.pid!
  assert.ok(unrelatedPid > 0)

  try {
    const defaultManager = new LocalTerminalSessionManager({
      isGroupAlive: () => true, // Group probe returns true for live unrelated process
      // Notice: NO validateGroupOwnership injected! Tests the concrete default validator.
    })

    // Simulate an ended session whose recorded leaderPid matches the unrelated live process PID
    ;(defaultManager as any).rememberEndedSession("term-default-reuse-check", unrelatedPid, {
      sessionId: "term-default-reuse-check",
      leaderPid: unrelatedPid,
      startToken: "expired-start-token-123",
      startedAtMs: Date.now() - 60000,
      command: ["node", "-e", "process.exit(0)"],
    })

    // Cleanup session on default manager (Call 1)
    const cleanRes1 = await defaultManager.cleanupSessions({
      cleanupId: "clean-default-reuse-1",
      scope: "single",
      sessionIds: ["term-default-reuse-check"],
    })
    assert.deepEqual(cleanRes1.failed_session_ids, ["term-default-reuse-check"], "call 1 unconfirmed cleanup reported failed")

    // Two-call regression: Call 2 MUST still report failed while unrelated process is alive
    const cleanRes2 = await defaultManager.cleanupSessions({
      cleanupId: "clean-default-reuse-2",
      scope: "single",
      sessionIds: ["term-default-reuse-check"],
    })
    assert.deepEqual(cleanRes2.failed_session_ids, ["term-default-reuse-check"], "call 2 still reported failed")

    // The unrelated process MUST still be alive and running (was NOT killed by the default manager)
    let isAlive = false
    try {
      process.kill(unrelatedPid, 0)
      isAlive = true
    } catch {}
    assert.equal(isAlive, true, "unrelated process remained alive and unharmed by default manager")
  } finally {
    try {
      unrelatedChild.kill("SIGKILL")
    } catch {}
  }
})
