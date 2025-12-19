import { PassThrough } from "node:stream"

export const createMockStdin = (): NodeJS.ReadStream => {
  const stream = new PassThrough()
  const mock = stream as NodeJS.ReadStream & {
    setRawMode?: (mode: boolean) => void
    ref?: () => NodeJS.ReadStream
    unref?: () => NodeJS.ReadStream
  }
  mock.isTTY = true
  mock.setRawMode = () => {}
  mock.ref = () => mock
  mock.unref = () => mock
  return mock
}

export const createWritableCapture = () => {
  const stream = new PassThrough()
  const frames: string[] = []
  stream.on("data", (chunk) => {
    frames.push(chunk.toString())
  })
  const writable = stream as unknown as NodeJS.WriteStream & { columns?: number }
  writable.columns = 100
  return {
    stream: writable,
    lastFrame: () => frames.at(-1) ?? "",
  }
}
