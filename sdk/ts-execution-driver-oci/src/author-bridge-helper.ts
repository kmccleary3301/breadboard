import { once } from "node:events"
import { createExecutionWorld, type AuthorWorkerCleanupResultV1, type AuthorWorkerResourceIdentityV1 } from "@breadboard/execution-drivers"
import { makeConfiguredOciExecutionDriver } from "./index.js"
import { AuthorFrameReader, encodeAuthorFrame, type OciAuthorChannelOptions } from "./author-bridge.js"
import { parseAuthorHelperSpec } from "./author-management.js"

type Intent = Parameters<NonNullable<OciAuthorChannelOptions["onIntent"]>>[0]
type Notice = Intent | AuthorWorkerResourceIdentityV1 | AuthorWorkerCleanupResultV1 | { readonly message: string }
function emit(kind: "INTENT" | "RECEIPT" | "CLEANUP" | "ERROR", value: Notice): void {
  process.stderr.write(`BREADBOARD_AUTHOR_${kind}\t${Buffer.from(JSON.stringify(value)).toString("base64")}\n`)
}
async function* parentFrames(): AsyncGenerator<Buffer> {
  const reader = new AuthorFrameReader()
  for await (const chunk of process.stdin) {
    reader.append(chunk)
    for (;;) {
      const frame = reader.take()
      if (frame === null) break
      yield frame
    }
  }
  reader.finish()
}
async function main(): Promise<void> {
  if (process.argv.length !== 2) throw new Error("author helper accepts its launch specification on stdin")
  const frames = parentFrames()
  const launch = await frames.next()
  if (launch.done) throw new Error("author helper requires one launch specification")
  const { input, runtimeCommand } = parseAuthorHelperSpec(launch.value.toString("utf8"))
  const world = createExecutionWorld({ drivers: [makeConfiguredOciExecutionDriver({ runtimeCommand })] })
  let channelId: string | null = null
  let closing: Promise<void> | null = null
  let stopping = false
  const close = (reason: string): Promise<void> => {
    stopping = true
    process.stdin.destroy()
    if (closing) return closing
    const id = channelId
    if (id === null) return Promise.resolve()
    closing = world.closeAuthorWorker(id, reason).then(result => { emit("CLEANUP", result.cleanup) })
    return closing
  }
  const stop = (): void => { void close("owner_channel_cancelled").catch(error => { emit("ERROR", { message: String(error) }) }) }
  process.once("SIGTERM", stop)
  process.once("SIGINT", stop)
  const committed = async (expected: string): Promise<void> => {
    if (stopping) throw new Error("Author launch was cancelled")
    const next = await frames.next()
    if (next.done || !next.value.equals(Buffer.from(expected))) throw new Error("Owner did not commit the author resource record")
    if (stopping) throw new Error("Author launch was cancelled")
  }
  try {
    const capabilityId = `author:${input.executionId}`
    const opened = await world.openAuthorWorker({
      capability: {
        schema_version: "bb.execution_capability.v1", capability_id: capabilityId,
        security_tier: "single_tenant", isolation_class: "oci", secret_mode: "scoped_proxy",
        evidence_mode: "minimal", tty_mode: "none", allow_net_hosts: [],
      },
      placement: {
        schema_version: "bb.execution_placement.v1", placement_id: input.executionId,
        placement_class: "local_oci", runtime_id: runtimeCommand, capability_id: capabilityId,
      },
      input,
      onIntent: async intent => { emit("INTENT", intent); await committed("intent_committed") },
      onReceipt: async identity => { emit("RECEIPT", identity); await committed("receipt_committed") },
    })
    if (opened.channelId === null) {
      if (opened.cleanup) emit("CLEANUP", opened.cleanup)
      throw new Error(`Author world refused launch: ${JSON.stringify(opened.unsupportedCase)}`)
    }
    channelId = opened.channelId
    if (stopping) throw new Error("Author launch was cancelled")
    const id = channelId
    const output = (async () => {
      for (;;) {
        const body = await world.readAuthorWorker(id)
        if (body === null) return
        if (!process.stdout.write(encodeAuthorFrame(body))) await once(process.stdout, "drain")
      }
    })()
    const incoming = (async () => {
      for await (const body of frames) await world.writeAuthorWorker(id, body)
    })()
    try {
      await Promise.race([output, incoming])
    } finally {
      await close("owner_channel_closed")
      await Promise.allSettled([output, incoming])
    }
  } finally {
    await close("helper_closed")
    process.removeListener("SIGTERM", stop)
    process.removeListener("SIGINT", stop)
  }
}
main().catch((error: unknown) => {
  emit("ERROR", { message: error instanceof Error ? error.message : "author helper failed" })
  process.exitCode = 1
})
