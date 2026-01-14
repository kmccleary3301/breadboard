import { spawn } from "node:child_process"
import net from "node:net"
import path from "node:path"
import { fileURLToPath } from "node:url"

import { createNdjsonParser, encodeLine } from "../ndjson.ts"
import { nowEnvelope } from "../protocol.ts"

type IpcInfo = { host: string; port: number; run_dir?: string }

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const readIpcInfoFromStderr = async (child: ReturnType<typeof spawn>, timeoutMs = 10_000): Promise<IpcInfo> => {
  let buffer = ""
  return await new Promise<IpcInfo>((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup()
      reject(new Error("Timed out waiting for --print-ipc payload"))
    }, timeoutMs)

    const onData = (chunk: Buffer) => {
      buffer += chunk.toString("utf8")
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ""
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          const parsed = JSON.parse(trimmed) as any
          if (parsed?.ipc?.host && parsed?.ipc?.port) {
            cleanup()
            resolve({ host: String(parsed.ipc.host), port: Number(parsed.ipc.port), run_dir: parsed.run_dir })
            return
          }
        } catch {
          // ignore
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

const connectAndHandshake = async (host: string, port: number): Promise<{ gotHello: boolean; gotState: boolean }> => {
  const socket = net.createConnection({ host, port })
  socket.setNoDelay(true)
  await new Promise<void>((resolve, reject) => {
    socket.once("connect", () => resolve())
    socket.once("error", (err) => reject(err))
  })

  let gotHello = false
  let gotState = false

  const parser = createNdjsonParser((obj) => {
    if (!obj || typeof obj !== "object") return
    const type = (obj as any).type
    if (type === "ctrl.hello") gotHello = true
    if (type === "ctrl.state") gotState = true
  })

  socket.on("data", (chunk) => parser.push(chunk.toString("utf8")))

  socket.write(encodeLine(nowEnvelope("ui.ready", {})))

  const started = Date.now()
  while (Date.now() - started < 3000) {
    if (gotHello && gotState) break
    await sleep(50)
  }

  try {
    socket.end()
  } catch {}

  return { gotHello, gotState }
}

const main = async () => {
  const scriptDir = path.dirname(fileURLToPath(import.meta.url))
  const controllerCwd = path.resolve(scriptDir, "../..")
  const child = spawn("bun", ["run", "phaseB/controller.ts", "--no-ui", "--print-ipc", "--exit-after-ms", "8000"], {
    cwd: controllerCwd,
    stdio: ["ignore", "ignore", "pipe"],
    env: { ...process.env, BREADBOARD_ENGINE_PREFER_BUNDLE: "0" },
  })

  const ipc = await readIpcInfoFromStderr(child)
  const result = await connectAndHandshake(ipc.host, ipc.port)

  if (!result.gotHello || !result.gotState) {
    child.kill("SIGTERM")
    throw new Error(`IPC contract smoke failed: ${JSON.stringify(result)}`)
  }

  try {
    child.kill("SIGTERM")
  } catch {}
}

await main()

