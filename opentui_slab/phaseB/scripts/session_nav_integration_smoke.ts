import http from "node:http"
import { spawn } from "node:child_process"
import net from "node:net"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { createNdjsonParser, encodeLine } from "../ndjson.ts"
import { nowEnvelope, type ControllerToUIMessage } from "../protocol.ts"

type IpcInfo = { host: string; port: number }

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const pickPort = async (): Promise<number> =>
  await new Promise<number>((resolve, reject) => {
    const server = net.createServer()
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("failed to resolve ephemeral port")))
        return
      }
      const port = address.port
      server.close(() => resolve(port))
    })
  })

const readBody = async (req: http.IncomingMessage): Promise<Record<string, unknown>> => {
  const chunks: Buffer[] = []
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))
  }
  const text = Buffer.concat(chunks).toString("utf8").trim()
  if (!text) return {}
  try {
    const parsed = JSON.parse(text)
    return typeof parsed === "object" && parsed ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

const waitForIpcInfo = async (child: ReturnType<typeof spawn>, timeoutMs = 12_000): Promise<IpcInfo> => {
  let buffer = ""
  return await new Promise<IpcInfo>((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup()
      reject(new Error("timed out waiting for controller --print-ipc payload"))
    }, timeoutMs)

    const onData = (chunk: Buffer) => {
      buffer += chunk.toString("utf8")
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ""
      for (const raw of lines) {
        const line = raw.trim()
        if (!line) continue
        try {
          const parsed = JSON.parse(line) as any
          const host = String(parsed?.ipc?.host ?? "").trim()
          const port = Number(parsed?.ipc?.port ?? 0)
          if (host && Number.isFinite(port) && port > 0) {
            cleanup()
            resolve({ host, port })
            return
          }
        } catch {
          // ignore non-json logs
        }
      }
    }

    const cleanup = () => {
      clearTimeout(timeout)
      child.stderr?.off("data", onData)
    }

    child.stderr?.on("data", onData)
  })
}

const main = async () => {
  const eventConnectionCounts: Record<string, number> = {}
  const commandLog: Array<{ sessionId: string; command: string }> = []

  const bridgePort = await pickPort()
  const bridgeBaseUrl = `http://127.0.0.1:${bridgePort}`

  const bridgeServer = http.createServer(async (req, res) => {
    const method = String(req.method || "GET").toUpperCase()
    const url = new URL(req.url || "/", bridgeBaseUrl)
    const pathname = url.pathname

    if (method === "GET" && pathname === "/health") {
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({ ok: true }))
      return
    }

    if (method === "POST" && pathname === "/sessions") {
      await readBody(req)
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({ session_id: "sess-main", status: "running" }))
      return
    }

    const commandMatch = pathname.match(/^\/sessions\/([^/]+)\/command$/)
    if (method === "POST" && commandMatch) {
      const sessionId = decodeURIComponent(commandMatch[1] || "")
      const body = await readBody(req)
      const command = String(body.command ?? "").trim()
      commandLog.push({ sessionId, command })

      if (command === "session_child_next") {
        res.writeHead(200, { "content-type": "application/json" })
        res.end(
          JSON.stringify({
            status: "ok",
            detail: {
              switched: true,
              target_session_id: "sess-child-1",
              child_session_id: "sess-child-1",
              parent_session_id: "sess-main",
              active_session_id: "sess-child-1",
            },
          }),
        )
        return
      }
      if (command === "session_parent") {
        res.writeHead(200, { "content-type": "application/json" })
        res.end(
          JSON.stringify({
            status: "ok",
            detail: {
              switched: true,
              target_session_id: "sess-main",
              child_session_id: "sess-child-1",
              parent_session_id: "sess-main",
              active_session_id: "sess-main",
            },
          }),
        )
        return
      }

      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({ status: "ok", detail: { switched: false } }))
      return
    }

    const inputMatch = pathname.match(/^\/sessions\/([^/]+)\/input$/)
    if (method === "POST" && inputMatch) {
      await readBody(req)
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({ status: "accepted" }))
      return
    }

    const eventMatch = pathname.match(/^\/sessions\/([^/]+)\/events$/)
    if (method === "GET" && eventMatch) {
      const sessionId = decodeURIComponent(eventMatch[1] || "")
      eventConnectionCounts[sessionId] = (eventConnectionCounts[sessionId] ?? 0) + 1
      res.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      })
      const payload = {
        id: `${sessionId}-evt-1`,
        seq: 1,
        type: "warning",
        session_id: sessionId,
        turn: 1,
        timestamp_ms: Date.now(),
        payload: { message: `connected-${sessionId}` },
      }
      res.write(`id: ${payload.id}\n`)
      res.write(`data: ${JSON.stringify(payload)}\n\n`)
      req.on("close", () => {
        try {
          res.end()
        } catch {
          // ignore
        }
      })
      return
    }

    if (method === "GET" && pathname === "/models") {
      res.writeHead(200, { "content-type": "application/json" })
      res.end(JSON.stringify({ models: [], default_model: null }))
      return
    }

    res.writeHead(404, { "content-type": "application/json" })
    res.end(JSON.stringify({ error: "not found", method, pathname }))
  })

  await new Promise<void>((resolve) => bridgeServer.listen(bridgePort, "127.0.0.1", resolve))

  const scriptDir = path.dirname(fileURLToPath(import.meta.url))
  const controllerCwd = path.resolve(scriptDir, "../..")
  const controller = spawn(
    "bun",
    [
      "run",
      "phaseB/controller.ts",
      "--no-ui",
      "--external-bridge",
      "--base-url",
      bridgeBaseUrl,
      "--print-ipc",
      "--exit-after-ms",
      "25000",
    ],
    {
      cwd: controllerCwd,
      stdio: ["ignore", "ignore", "pipe"],
      env: { ...process.env, BREADBOARD_ENGINE_PREFER_BUNDLE: "0" },
    },
  )

  let socket: net.Socket | null = null
  try {
    const ipc = await waitForIpcInfo(controller)
    socket = net.createConnection({ host: ipc.host, port: ipc.port })
    socket.setNoDelay(true)
    await new Promise<void>((resolve, reject) => {
      socket!.once("connect", () => resolve())
      socket!.once("error", reject)
    })

    const states: Array<{ active_session_id: string | null; base_url: string | null; status: string | null }> = []
    const parser = createNdjsonParser((obj) => {
      const message = obj as ControllerToUIMessage
      if (!message || typeof message !== "object") return
      if (message.type !== "ctrl.state") return
      const payload = (message.payload ?? {}) as Record<string, unknown>
      const active = typeof payload.active_session_id === "string" ? payload.active_session_id : null
      const baseUrl = typeof payload.base_url === "string" ? payload.base_url : null
      const status = typeof payload.status === "string" ? payload.status : null
      states.push({ active_session_id: active, base_url: baseUrl, status })
    })
    socket.on("data", (chunk) => parser.push(chunk.toString("utf8")))

    socket.write(encodeLine(nowEnvelope("ui.ready", {})))

    const waitForBridgeReady = async (timeoutMs = 7000) => {
      const started = Date.now()
      while (Date.now() - started < timeoutMs) {
        if (states.some((s) => Boolean(s.base_url) && (s.status === "idle" || s.status === "running"))) return
        await sleep(40)
      }
      throw new Error("timed out waiting for bridge-ready controller state")
    }
    await waitForBridgeReady()

    socket.write(encodeLine(nowEnvelope("ui.submit", { text: "smoke: session nav integration" })))

    const waitForState = async (sessionId: string, timeoutMs = 6000, fromIndex = 0) => {
      const started = Date.now()
      while (Date.now() - started < timeoutMs) {
        if (states.slice(fromIndex).some((s) => s.active_session_id === sessionId)) return
        await sleep(40)
      }
      throw new Error(`timed out waiting for active_session_id=${sessionId}`)
    }
    const waitForCommand = async (command: string, timeoutMs = 4000) => {
      const started = Date.now()
      while (Date.now() - started < timeoutMs) {
        if (commandLog.some((entry) => entry.command === command)) return
        await sleep(25)
      }
      throw new Error(`timed out waiting for command=${command}`)
    }

    await waitForState("sess-main")

    socket.write(
      encodeLine(
        nowEnvelope("ui.command", {
          name: "session_child_next",
          args: { child_session_id: "sess-child-1", parent_session_id: "sess-main" },
        }),
      ),
    )
    await waitForCommand("session_child_next")
    await waitForState("sess-child-1")

    const beforeParentStateIndex = states.length
    socket.write(
      encodeLine(
        nowEnvelope("ui.command", {
          name: "session_parent",
          args: { child_session_id: "sess-child-1", parent_session_id: "sess-main" },
        }),
      ),
    )
    await waitForCommand("session_parent")
    await waitForState("sess-main", 6000, beforeParentStateIndex)

    const mainConnections = eventConnectionCounts["sess-main"] ?? 0
    const childConnections = eventConnectionCounts["sess-child-1"] ?? 0
    if (mainConnections < 1 || childConnections < 1) {
      throw new Error(
        `SSE rebind not observed (sess-main=${mainConnections}, sess-child-1=${childConnections})`,
      )
    }

    const childNextCalls = commandLog.filter(
      (entry) => entry.sessionId === "sess-main" && entry.command === "session_child_next",
    ).length
    if (childNextCalls < 1) {
      throw new Error("session_child_next command was not posted to bridge")
    }
    const parentCalls = commandLog.filter((entry) => entry.command === "session_parent").length
    if (parentCalls < 1) {
      throw new Error("session_parent command was not posted to bridge")
    }

    socket.write(encodeLine(nowEnvelope("ui.shutdown", {})))
    console.error(
      `[session-nav-integration-smoke] pass (connections: main=${mainConnections}, child=${childConnections})`,
    )
  } finally {
    try {
      socket?.end()
    } catch {
      // ignore
    }
    try {
      controller.kill("SIGTERM")
    } catch {
      // ignore
    }
    await new Promise<void>((resolve) => bridgeServer.close(() => resolve()))
  }
}

await main()
