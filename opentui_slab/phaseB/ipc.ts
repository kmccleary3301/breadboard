import net from "node:net"
import { encodeLine, createNdjsonParser } from "./ndjson.ts"

export type IpcConnection = {
  readonly remote: string
  send: (msg: unknown) => void
  close: () => void
  onMessage: (handler: (msg: unknown) => void) => () => void
  onClose: (handler: () => void) => () => void
}

export const createIpcServer = async (options?: {
  readonly host?: string
  readonly port?: number
}): Promise<{
  host: string
  port: number
  close: () => Promise<void>
  onConnection: (handler: (conn: IpcConnection) => void) => () => void
}> => {
  const host = options?.host ?? "127.0.0.1"

  const connections = new Set<net.Socket>()
  const connectionHandlers = new Set<(conn: IpcConnection) => void>()

  const server = net.createServer((socket) => {
    connections.add(socket)
    socket.setNoDelay(true)
    socket.setKeepAlive(true)

    const msgHandlers = new Set<(msg: unknown) => void>()
    const closeHandlers = new Set<() => void>()

    const parser = createNdjsonParser((msg) => {
      for (const handler of msgHandlers) handler(msg)
    })

    socket.on("data", (chunk) => {
      parser.push(chunk.toString("utf8"))
    })

    const onSocketClose = () => {
      connections.delete(socket)
      parser.flush()
      for (const handler of closeHandlers) handler()
    }

    socket.on("close", onSocketClose)
    socket.on("end", onSocketClose)
    socket.on("error", () => onSocketClose())

    const conn: IpcConnection = {
      remote: `${socket.remoteAddress ?? "?"}:${socket.remotePort ?? "?"}`,
      send: (msg) => {
        try {
          socket.write(encodeLine(msg))
        } catch {
          // ignore
        }
      },
      close: () => {
        try {
          socket.end()
        } catch {
          // ignore
        }
      },
      onMessage: (handler) => {
        msgHandlers.add(handler)
        return () => void msgHandlers.delete(handler)
      },
      onClose: (handler) => {
        closeHandlers.add(handler)
        return () => void closeHandlers.delete(handler)
      },
    }

    for (const handler of connectionHandlers) handler(conn)
  })

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject)
    server.listen(options?.port ?? 0, host, () => resolve())
  })

  const addr = server.address()
  if (!addr || typeof addr === "string") {
    throw new Error("Unable to resolve IPC server address.")
  }

  return {
    host,
    port: addr.port,
    close: async () => {
      for (const socket of connections) {
        try {
          socket.destroy()
        } catch {
          // ignore
        }
      }
      await new Promise<void>((resolve) => server.close(() => resolve()))
    },
    onConnection: (handler) => {
      connectionHandlers.add(handler)
      return () => void connectionHandlers.delete(handler)
    },
  }
}

export const connectIpcClient = async (options: {
  readonly host: string
  readonly port: number
  readonly retryMs?: number
  readonly timeoutMs?: number
}): Promise<IpcConnection> => {
  const started = Date.now()
  const timeoutMs = options.timeoutMs ?? 20_000
  const retryMs = options.retryMs ?? 150

  for (;;) {
    try {
      const socket = net.createConnection({ host: options.host, port: options.port })
      socket.setNoDelay(true)
      socket.setKeepAlive(true)

      await new Promise<void>((resolve, reject) => {
        socket.once("connect", () => resolve())
        socket.once("error", (err) => reject(err))
      })

      const msgHandlers = new Set<(msg: unknown) => void>()
      const closeHandlers = new Set<() => void>()
      const parser = createNdjsonParser((msg) => {
        for (const handler of msgHandlers) handler(msg)
      })

      socket.on("data", (chunk) => {
        parser.push(chunk.toString("utf8"))
      })

      const onSocketClose = () => {
        parser.flush()
        for (const handler of closeHandlers) handler()
      }
      socket.on("close", onSocketClose)
      socket.on("end", onSocketClose)
      socket.on("error", () => onSocketClose())

      return {
        remote: `${options.host}:${options.port}`,
        send: (msg) => {
          try {
            socket.write(encodeLine(msg))
          } catch {
            // ignore
          }
        },
        close: () => {
          try {
            socket.end()
          } catch {
            // ignore
          }
        },
        onMessage: (handler) => {
          msgHandlers.add(handler)
          return () => void msgHandlers.delete(handler)
        },
        onClose: (handler) => {
          closeHandlers.add(handler)
          return () => void closeHandlers.delete(handler)
        },
      }
    } catch {
      if (Date.now() - started > timeoutMs) {
        throw new Error(`IPC connect timeout after ${timeoutMs}ms.`)
      }
      await new Promise((resolve) => setTimeout(resolve, retryMs))
    }
  }
}

