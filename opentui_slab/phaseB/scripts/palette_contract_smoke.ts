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

const connectAndRequestCommandsModelsAndFiles = async (
  host: string,
  port: number,
): Promise<{
  gotCommands: boolean
  commandsCount: number
  gotModels: boolean
  modelsCount: number
  gotFiles: boolean
  filesCount: number
}> => {
  const socket = net.createConnection({ host, port })
  socket.setNoDelay(true)
  await new Promise<void>((resolve, reject) => {
    socket.once("connect", () => resolve())
    socket.once("error", (err) => reject(err))
  })

  let gotCommands = false
  let gotModels = false
  let gotFiles = false
  let commandsCount = 0
  let modelsCount = 0
  let filesCount = 0

  const parser = createNdjsonParser((obj) => {
    if (!obj || typeof obj !== "object") return
    if ((obj as any).type !== "ctrl.palette.items") return
    const payload = (obj as any).payload ?? {}
    const items = payload.items
    if (!Array.isArray(items)) return
    if (payload.kind === "commands") {
      gotCommands = true
      commandsCount = items.length
      socket.write(encodeLine(nowEnvelope("ui.palette.open", { kind: "models", query: "" })))
      return
    }
    if (payload.kind === "models") {
      if (items.length > 0) {
        gotModels = true
        modelsCount = items.length
        socket.write(encodeLine(nowEnvelope("ui.palette.open", { kind: "files", query: "README" })))
      }
      return
    }
    if (payload.kind === "files") {
      if (items.length > 0) {
        gotFiles = true
        filesCount = items.length
      }
    }
  })

  socket.on("data", (chunk) => parser.push(chunk.toString("utf8")))

  socket.write(encodeLine(nowEnvelope("ui.ready", {})))
  socket.write(encodeLine(nowEnvelope("ui.palette.open", { kind: "commands", query: "" })))

  const started = Date.now()
  while (Date.now() - started < 25_000) {
    if (gotCommands && gotModels && gotFiles && modelsCount > 0 && filesCount > 0) break
    await sleep(50)
  }

  try {
    socket.end()
  } catch {}

  return { gotCommands, commandsCount, gotModels, modelsCount, gotFiles, filesCount }
}

const main = async () => {
  const scriptDir = path.dirname(fileURLToPath(import.meta.url))
  const controllerCwd = path.resolve(scriptDir, "../..")
  const child = spawn("bun", ["run", "phaseB/controller.ts", "--no-ui", "--print-ipc", "--exit-after-ms", "8000"], {
    cwd: controllerCwd,
    stdio: ["ignore", "ignore", "pipe"],
    env: { ...process.env, BREADBOARD_ENGINE_PREFER_BUNDLE: "0", BREADBOARD_WORKSPACE: controllerCwd },
  })

  const ipc = await readIpcInfoFromStderr(child)
  const result = await connectAndRequestCommandsModelsAndFiles(ipc.host, ipc.port)

  if (
    !result.gotCommands ||
    result.commandsCount <= 0 ||
    !result.gotModels ||
    result.modelsCount <= 0 ||
    !result.gotFiles ||
    result.filesCount <= 0
  ) {
    child.kill("SIGTERM")
    throw new Error(`palette contract smoke failed: ${JSON.stringify(result)}`)
  }

  try {
    child.kill("SIGTERM")
  } catch {}
}

await main()

